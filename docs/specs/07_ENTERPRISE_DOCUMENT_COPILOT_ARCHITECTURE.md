# ĐẶC TẢ KIẾN TRÚC TOÀN DIỆN: ENTERPRISE DOCUMENT COPILOT (SPEC-ARCH-07)
## HỆ THỐNG BIÊN TẬP TÀI LIỆU CẤP ĐỘ DOANH NGHIỆP: HIỆU NĂNG CAO, BẢO TOÀN NHỊ PHÂN & HỘI THOẠI ĐA LƯỢT

**Mã tài liệu:** `SPEC-ARCH-07`  
**Phiên bản:** `3.0.0-ENTERPRISE`  
**Trạng thái:** Approved Architecture Blueprint  
**Phạm vi:** Toàn bộ Monorepo (FastAPI Serverless, Workers, Patchers, RAG & Egress Pipelines)

---

## 1. TỔNG QUAN KIẾN TRÚC HỆ THỐNG (SYSTEM ARCHITECTURE)

Hệ thống được thiết kế theo mô hình **Reactive Multi-Layer Document Copilot** kết hợp giữa **In-Memory Shadow Mutation** và **Asynchronous Storage Egress**:

```text
[ USER QUERY / INTERACTION ]
            │
            ▼
┌────────────────────────────────────────────────────────┐
│ TẦNG 1: EPISODIC CONTEXT ACCUMULATOR & ANCHOR MAP      │
│ - Tích lũy Focus State (sheet, hàng, ô vừa thao tác)   │
│ - Anchor Map: Điều hướng theo mục lục Word / Block XLSX│
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ TẦNG 2: SMART RESOLVER & TOOL CALLING GATEWAY          │
│ - Fuzzy Header Matching (Levenshtein Token Ratio)      │
│ - Gemini / Claude Native Tool Calling (Strict Schema)  │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ TẦNG 3: PRE-FLIGHT INTEGRITY FIREWALL                  │
│ - Kiểm tra DAG phụ thuộc công thức (Formula Break Check)│
│ - Kiểm tra kiểu dữ liệu & Circular Reference           │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ TẦNG 4: IN-MEMORY TWO-PHASE COMMIT ENGINE              │
│ - Phase 1: In-Memory Shadow Buffer (io.BytesIO, <50ms) │
│ - Phase 2: Shadow Recalculation -> Trả Diff Preview    │
└───────────────────────────┬────────────────────────────┘
                            │
              ┌─────────────┴─────────────┐
              ▼ (Người dùng HỦY)          ▼ (Người dùng XÁC NHẬN)
         Hủy Preview                Commit Ghi Nhị Phân
         Giữ nguyên file            + Snapshot Version Ledger
                                    + Debounced Background Sync (Drive)
```

---

## 2. CHI TIẾT 10 TRỤ CỘT KIẾN TRÚC ENTERPRISE

### Trụ cột 1: Two-Phase Commit với Optimistic Dry-Run (Staged Preview)
- **Vấn đề:** Tránh việc AI sửa nhầm hàng chục ô mà không có cơ hội kiểm tra trước khi ghi đè tệp nhị phân.
- **Cơ chế:**
  - `POST /api/documents/{doc_id}/office/dry-run`: LLM phát sinh mutations, hệ thống mô phỏng patch trên bản sao bộ nhớ và trả về `DiffPreview`:
    ```json
    {
      "preview_id": "prev_a982f1",
      "affected_count": 2,
      "changes": [
        {"target": "C5", "label": "Đơn giá", "old": "200.000", "new": "220.000"},
        {"target": "E5", "label": "Thành tiền", "old": "=C5*D5 (10.000.000)", "new": "=C5*D5 (11.000.000)", "is_formula": true}
      ]
    }
    ```
  - `POST /api/documents/{doc_id}/office/commit`: Xác nhận áp dụng chính thức vào tệp.

### Trụ cột 2: Realtime Streaming Tool Execution (SSE Granular Events)
- **Vấn đề:** Các tác vụ phân tích, gọi LLM, tính toán và đồng bộ mất 3–6s, cần cập nhật trạng thái liên tục để UI không bị đơ.
- **Sự kiện SSE:** `analyzing_context` (15%) $\rightarrow$ `matching_headers` (35%) $\rightarrow$ `generating_mutations` (65%) $\rightarrow$ `shadow_evaluating` (85%) $\rightarrow$ `completed` (100%).

### Trụ cột 3: Smart Header Fuzzy Resolver (Ánh xạ Tiêu đề Cột Ngữ nghĩa)
- **Vấn đề:** Người dùng dùng từ đồng nghĩa (*"giá tiền"*, *"đơn giá"*, *"chi phí"*) không khớp 100% với header trong Excel (`"Đơn giá (VNĐ)"`).
- **Cơ chế:** Khi ingest file, bóc tách header và lập index nhẹ. Áp dụng Token Set Ratio ($\ge 75\%$) để tự động map về đúng ký tự cột (`C`) trước khi đưa vào context LLM.

### Trụ cột 4: Shadow Recalculation Engine (Tính toán công thức tức thì cho Preview)
- **Vấn đề:** `openpyxl` không tự động nhảy số công thức `<v>` trong bộ nhớ, Markdown preview hiển thị chuỗi text `=C5*D5`.
- **Cơ chế:** Tích hợp engine tính toán nhanh trong Python (`simpleeval` / `formulas`) để đánh giá các biểu thức số học (`SUM`, `AVERAGE`, `A*B`) ngay khi patch in-memory, hiển thị giá trị tính toán trước cho người dùng.

### Trụ cột 5: Snapshot State Ledger & Rollback Granularity (Kiểm soát phiên bản)
- **Vấn đề:** Người dùng muốn Undo/Redo về phiên bản trước khi đổi dữ liệu.
- **Cơ chế:** Bảng `document_snapshots` lưu trữ `version_number`, `storage_path`, `applied_mutation`, và `prompt_trigger`. Hỗ trợ API `POST /rollback` để lùi về bất kỳ phiên bản nào trong lịch sử.

### Trụ cột 6: In-Memory Shadow Buffer & Debounced Async Egress (Tách tải I/O)
- **Vấn đề:** Tránh thắt cổ chai I/O đĩa và timeout khi người dùng chat liên tiếp nhiều câu.
- **Cơ chế:**
  - Patch trực tiếp trên `io.BytesIO` buffer trong RAM (thời gian xử lý 50–150ms).
  - Tách luồng upload Google Drive và Outline sang Background Worker với cơ chế **Debounce 5 giây**: Gộp nhiều lượt sửa liên tiếp thành 1 lần đẩy Drive duy nhất.

### Trụ cột 7: Multi-Turn Mutation Context Accumulator (Tích lũy ngữ cảnh nhiều lượt chat)
- **Vấn đề:** Người dùng ra lệnh rời rạc: *"Xem dòng 3"* $\rightarrow$ *"Tăng nó lên 20% và nhân đôi số lượng"*.
- **Cơ chế (Episodic Working Memory):**
  Lưu `focus_state` của phiên làm việc:
  ```json
  {
    "active_document_id": "doc_9921",
    "focus_state": {
      "last_sheet": "Sheet1",
      "last_row_index": 3,
      "last_entities": {"item": "Gói Cloud Pro", "price_cell": "D3", "qty_cell": "C3"}
    }
  }
  ```
  Tự động nạp vào prompt lượt kế tiếp để giải quyết đại từ thay thế (*"nó"*, *"dòng đó"*).

### Trụ cột 8: XML Run Fragment Re-joining (Bảo toàn định dạng vi mô trong Word)
- **Vấn đề:** Cụm từ `150.000.000 VNĐ` bị phân mảnh trong nhiều thẻ `<w:r>` làm mất formatting nếu thay thế bằng `paragraph.text`.
- **Cơ chế:** Thuật toán **Run-Span Boundary Matcher**: Ghép toàn bộ run text, tìm span `(start_char, end_char)`, inject chuỗi mới vào run đầu tiên (giữ 100% thuộc tính font/color/bold), và dọn sạch các ký tự bị cắt ở các run tiếp theo mà không làm vỡ cấu trúc XML DOM.

### Trụ cột 9: Semantic Formula Integrity Firewall (Tường lửa phân tích đồ thị công thức)
- **Vấn đề:** Ô bị sửa đang nuôi dữ liệu cho 10 ô công thức ở các sheet khác, đổi sai kiểu dữ liệu sẽ sinh lỗi `#VALUE!`, `#REF!`.
- **Cơ chế:** Trích xuất đồ thị phụ thuộc (DAG). Trước khi patch:
  - Kiểm tra tương thích kiểu dữ liệu (Numeric vs String).
  - Kiểm tra gãy dải tham chiếu (Range Break).
  - Chặn và phát cảnh báo giải thích nếu phát hiện rủi ro vỡ công thức kế toán.

### Trụ cột 10: Semantic Chunking & Anchor Map cho tài liệu lớn (Tối ưu Context Window)
- **Vấn đề:** File Word 50 trang hoặc Excel 20 sheet làm tràn context window của LLM.
- **Cơ chế (Two-Stage Retrieval):**
  - **Chặng 1 (Anchor Map):** Bóc tách mục lục phân cấp (Word) hoặc Sheet Skeleton theo khối bảng `table_blocks` (Excel) tốn cực ít token.
  - **Chặng 2 (Targeted Injection):** LLM chọn đúng `anchor_id` hoặc `range` cần can thiệp, hệ thống chỉ inject duy nhất block dữ liệu đó vào prompt để sinh mutation chính xác 100%.

---

## 3. LỘ TRÌNH TRIỂN KHAI DOANH NGHIỆP (ROADMAP)

| Gói triển khai | Các kỹ thuật bao gồm | Mục tiêu đạt được |
| :--- | :--- | :--- |
| **Package 1: Core Precision & Safety** | Trụ cột 1 (Two-Phase Commit) + Trụ cột 3 (Smart Header Resolver) | Xem trước Diff trước khi lưu, khử lỗi trượt cột khi dùng từ đồng nghĩa. |
| **Package 2: High Performance & I/O** | Trụ cột 6 (In-Memory Buffer & Async Sync) + Trụ cột 2 (SSE Streaming) | Phản hồi dưới 500ms, UI hiển thị thanh tiến trình realtime từng bước. |
| **Package 3: Conversational Intelligence** | Trụ cột 7 (Context Accumulator) + Trụ cột 10 (Anchor Map cho file lớn) | Chat tự nhiên nhiều lượt ("sửa dòng đó"), xử lý mượt mà tài liệu 50 trang. |
| **Package 4: Zero-Breakage Integrity** | Trụ cột 8 (XML Run Re-joining) + Trụ cột 9 (Formula Firewall) + Trụ cột 5 (Rollback Ledger) | Chống vỡ công thức `#VALUE!`, bảo vệ font Word, hỗ trợ Undo/Redo tức thì. |

