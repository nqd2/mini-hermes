# ĐẶC TẢ KIẾN TRÚC & SRS: OFFICE ASSISTANT V2 GENERALIZED
## CHUYỂN ĐỔI TỔNG QUÁT HÓA, LOẠI BỎ HEURISTIC CREEP & PHÒNG NGỪA TÁI NHIỄM DỮ LIỆU MẪU

**Mã tài liệu:** `SPEC-ARCH-06`  
**Trạng thái:** Approved / Production Baseline  
**Phạm vi áp dụng:** Toàn bộ monorepo (đặc biệt `packages/api-serverless` và `packages/orchestrator-worker`)

---

## 1. 4 NGUYÊN TẮC BẤT BIẾN (STRICT INVARIANCES)

Mọi đóng góp mã nguồn (cả nhân viên và AI Assistant) phải tuân thủ tuyệt đối 4 nguyên tắc sau:

### 1.1. Nguyên tắc 1: Zero Mock Data / Zero Hardcode
- **Nghiêm cấm tuyệt đối:** Đưa bất kỳ tên đối tác, tên sản phẩm, mã danh mục, hoặc hằng số cụ thể từ file dữ liệu test/mẫu vào logic code (`"polo"`, `"áo"`, `"weamis"`, `"sp-"`, `"fpt"`, `"180000"`, `"iga"`, `"cv"`,...).
- **Cơ chế bắt buộc:** Mọi thông tin hàng, cột, ô tính, đoạn văn, và placeholder phải được **trích xuất động (Dynamic Extraction)** từ cấu trúc thực tế của tệp nhị phân nạp vào bộ nhớ.

### 1.2. Nguyên tắc 2: Zero Heuristic Regex Routing (Context-First)
- **Nghiêm cấm tuyệt đối:** Viết các mảng từ khóa tĩnh kiểu `has_verb`, `has_target` để đoán xem người dùng có muốn sửa tài liệu hay không.
- **Cơ chế bắt buộc (Context Gate):**
  - Chỉ kích hoạt và nạp tool xử lý tài liệu khi request có **Ngữ cảnh Trạng thái (State/Context)**:
    1. Người dùng đính kèm file trong request (`attached_doc_ids`).
    2. Phiên làm việc có tài liệu đang chọn (`session.active_document_id`).
    3. Người dùng chỉ định rõ tên file tồn tại bằng cú pháp định danh (`@filename.xlsx`).
  - Quyết định có sửa hay không và sửa ô nào là thẩm quyền của LLM thông qua **Function Calling (Strict Schema)**, không phải của tầng if/else regex trong Python.

### 1.3. Nguyên tắc 3: Fail-Fast trên trích xuất lỗi (No Fallback Invention)
- **Nghiêm cấm tuyệt đối:** Viết các hàm fallback tự đoán dữ liệu, tự gán giá trị mặc định, tự gán dòng 4, dòng 6 hoặc tự điền giá trị khi LLM không parse được thao tác.
- **Cơ chế bắt buộc:** Khi LLM parse lỗi, tọa độ không hợp lệ, hoặc không tìm thấy mục tiêu: **Raise Exception có cấu trúc (hoặc trả về Structured Error)** để thông báo minh bạch cho người dùng, giữ nguyên 100% tính toàn vẹn của tệp gốc.

### 1.4. Nguyên tắc 4: Không tự sinh file giả khi thiếu file
- **Nghiêm cấm tuyệt đối:** Tự động khởi tạo file mẫu chứa dữ liệu giả định (`Bang_Bao_Gia_Dong_Phuc_Weamis.xlsx`) khi không tìm thấy file.
- **Cơ chế bắt buộc:** Nếu `file_id` không tồn tại, trả về `404 Document Not Found` hoặc yêu cầu người dùng tải tệp lên.

---

## 2. KIẾN TRÚC ĐIỀU PHỐI (CONTEXT-FIRST ROUTING ARCHITECTURE)

```text
[User Request: prompt, attached_doc_ids, session_id]
                       │
                       ▼
    ┌──────────────────────────────────────────────┐
    │ Tầng 1: Deterministic Context Gate (0 token) │
    │ - attached_doc_ids có file Office hợp lệ?    │
    │ - session có active_document_id hợp lệ?      │
    └──────────────────────┬───────────────────────┘
                           │
             ┌─────────────┴─────────────┐
             ▼ (Có Document Context)     ▼ (Không có Document Context)
    ┌─────────────────────────────┐   ┌─────────────────────────────┐
    │ Nạp Tool Mutation vào LLM:  │   │ General Chat RAG (No Tool)  │
    │ mutate_office_document      │   │ Phân tích / Trả lời thuần   │
    │ (LLM tự quyết định mutate   │   └─────────────────────────────┘
    │  hoặc trả lời giải thích)   │
    └──────────────┬──────────────┘
                   │
                   ▼ (LLM Function Call)
    ┌──────────────────────────────────────────────┐
    │ Tầng 2: Delta Compiler & Strict Validator    │
    │ - Kiểm tra regex tọa độ ô: ^[A-Z]{1,3}[1-9]  │
    │ - Guardrail: Cấm Circular Reference          │
    │ - Guardrail: Cấm ghi đè formula bằng static  │
    └──────────────────────┬───────────────────────┘
                           │
                           ▼ (Valid Deltas)
    ┌──────────────────────────────────────────────┐
    │ Tầng 3: Deterministic Binary Patching        │
    │ - XlsxDeltaPatcher / WordDocxPatcher         │
    │ - openpyxl fullCalcOnLoad = True             │
    │ - OpenXML Run-Span Preservation              │
    └──────────────────────┬───────────────────────┘
                           │
                           ▼
    ┌──────────────────────────────────────────────┐
    │ Tầng 4: Storage & Knowledge Sync             │
    │ - Google Drive: 03_Final_Deliverables        │
    │ - Zilliz Cloud: Re-index Table Vector Chunks │
    └──────────────────────────────────────────────┘
```

---

## 3. CANONICAL TOOL SCHEMA (DOCUMENT MUTATION)

```json
{
  "name": "mutate_office_document",
  "description": "Thực hiện chỉnh sửa delta trên tài liệu Office hiện tại (Excel .xlsx hoặc Word .docx). Chỉ kích hoạt khi người dùng yêu cầu thay đổi, cập nhật hoặc tính toán lại dữ liệu trong file.",
  "parameters": {
    "type": "object",
    "properties": {
      "summary": {
        "type": "string",
        "description": "Tóm tắt ngắn gọn các hành động bằng tiếng Việt"
      },
      "operations": {
        "type": "array",
        "description": "Danh sách các thao tác DocumentMutationBatch",
        "items": {
          "type": "object",
          "properties": {
            "op": {
              "type": "string",
              "enum": ["set_cell_value", "insert_row", "delete_row", "replace_text"]
            },
            "target": {
              "type": "object",
              "properties": {
                "sheet": { "type": "string" },
                "cell": { "type": "string", "description": "Tọa độ ô ví dụ 'C5', 'D10'" },
                "row": { "type": "integer" },
                "match_pattern": {
                  "type": "object",
                  "properties": {
                    "find": { "type": "string" },
                    "occurrence": { "type": "integer" }
                  }
                }
              }
            },
            "value": { "description": "Giá trị mới (chuỗi, số hoặc boolean)" },
            "guards": {
              "type": "object",
              "properties": {
                "prevent_formula_overwrite": { "type": "boolean" }
              }
            }
          },
          "required": ["op"]
        }
      }
    },
    "required": ["summary", "operations"]
  }
}
```

---

## 4. CHECKLIST NGHIỆM THU MÃ NGUỒN (DEFINITION OF DONE)

1. [ ] Không còn bất kỳ từ khóa mẫu nào (`polo`, `áo`, `sp-`, `weamis`, `fpt`, `180000`, `tạp dề`, `mũ`) trong `packages/api-serverless/src/`.
2. [ ] Các mảng `has_verb`, `has_target` bị xóa bỏ hoàn toàn.
3. [ ] Không có hàm `_fallback_excel_rules` hay `_fallback_word_rules` tự đoán.
4. [ ] Test suite `pytest packages/api-serverless/tests/` pass 100% trên dữ liệu động.

