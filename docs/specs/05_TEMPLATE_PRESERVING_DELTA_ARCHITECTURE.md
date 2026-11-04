# ĐẶC TẢ KIẾN TRÚC: TEMPLATE-PRESERVING DELTA MUTATION (DUAL-STORE PIPELINE)

**Mã tài liệu:** SPEC-ARCH-05  
**Trạng thái:** Approved / Implementation Baseline  
**Mục tiêu:** Giải quyết triệt để vấn đề mất định dạng, mất công thức và lẫn rác HTML khi xử lý tài liệu Word (.docx), Excel (.xlsx) và PDF trong Mini-Hermes MVP.

---

## 1. PHÂN TÍCH BẢN CHẤT 4 CẠM BẪY KỸ THUẬT & GIẢI PHÁP TRIỆT ĐỂ

### 1.1. Cạm bẫy 1: Word XML Run-Boundary Splitting (Chuỗi bị chia cắt giữa các Run)
- **Bản chất OpenXML:** Trong file `.docx`, một đoạn văn (`<w:p>`) không lưu chuỗi văn bản liên tục mà chia thành các `<w:r>` (runs) chứa `<w:t>` (text). Do quá trình người dùng gõ phím, sửa lỗi chính tả hoặc copy-paste, một cụm từ như `"200.000.000 VNĐ"` thường bị Microsoft Word xé nhỏ thành nhiều run riêng biệt:
  ```xml
  <w:p>
    <w:r><w:t>Giá trị hợp đồng là: </w:t></w:r>
    <w:r><w:rPr><w:b/></w:rPr><w:t>200.</w:t></w:r>
    <w:r><w:rPr><w:b/></w:rPr><w:t>000.</w:t></w:r>
    <w:r><w:rPr><w:b/></w:rPr><w:t>000 VNĐ</w:t></w:r>
  </w:p>
  ```
- **Hậu quả nếu replace thông thường:**
  - Nếu quét `if find_text in run.text`: Không bao giờ tìm thấy vì `"200.000.000 VNĐ"` không nằm trọn trong bất kỳ run đơn lẻ nào.
  - Nếu gán đè `paragraph.text = new_text`: Thư viện sẽ xóa sạch toàn bộ các thẻ con `<w:r>`, làm **mất 100% định dạng in đậm, in nghiêng, màu sắc và font chữ riêng** của đoạn văn!
- **Giải pháp Triệt để của Mini-Hermes:**
  - Áp dụng thuật toán **Run-Span Boundary Matcher**:
    1. Ghép nối text của toàn bộ các run trong paragraph để tạo chuỗi toàn văn `full_text` và lưu vết chỉ số index ký tự tương ứng của từng run `(run_start_char, run_end_char)`.
    2. Dùng regex tìm vị trí xuất hiện của cụm từ cần thay thế: `(match_start, match_end)`.
    3. Xác định tập hợp các run bị ảnh hưởng từ `run_first` đến `run_last`.
    4. Ghi đè chuỗi mới vào `run_first` (giữ nguyên toàn bộ `<w:rPr>` thuộc tính font, màu, in đậm của run này) và dọn sạch các ký tự bị cắt ở các run tiếp theo trong khoảng mà không làm ảnh hưởng đến các run phía sau.

---

### 1.2. Cạm bẫy 2: Excel Formula Cache & Thuộc tính `<v>` (Recalculation Invariant)
- **Bản chất OpenXML:** File `.xlsx` lưu trữ hai thông tin riêng biệt cho một ô công thức:
  - `<f>`: Chuỗi công thức (ví dụ: `SUM(D2:D10)` hoặc `B2*C2`).
  - `<v>`: Giá trị đã tính toán trước đó (cached value, ví dụ: `15000000`).
- **Hậu quả nếu chỉ sửa ô dữ liệu:**
  - Khi dùng `openpyxl` cập nhật ô đơn giá `C2 = 180000`, ô thành tiền `D2` vẫn giữ nguyên `<v>15000000</v>`. Khi xem trên trình xem trước (Quick Preview / Google Drive Preview), số liệu tổng hợp không tự động nhảy cho đến khi mở bằng ứng dụng đầy đủ và bấm Save.
- **Giải pháp Triệt để của Mini-Hermes:**
  1. Luôn load workbook bằng `openpyxl.load_workbook(file_path, data_only=False)` để bảo vệ 100% chuỗi công thức `<f>` không bị chuyển hóa thành số chết.
  2. Kích hoạt cờ bắt buộc tính toán lại của OpenXML ngay khi mở file:
     ```python
     wb.calculation_properties.fullCalcOnLoad = True
     wb.calculation_properties.calcMode = "auto"
     ```
  3. Cơ chế Formula Protection Guardrail: Tự động kiểm tra ô mục tiêu; nếu ô đang chứa chuỗi bắt đầu bằng dấu `=`, hệ thống **từ chối ghi đè giá trị tĩnh** và gửi cảnh báo để bảo vệ logic kế toán.

---

### 1.3. Cạm bẫy 3: Outline Wiki Model (Semantic Index vs. Binary Storage)
- **Vấn đề:** Outline Wiki là trình soạn thảo dạng tài liệu cây (ProseMirror/Markdown). Việc nhồi nhét hàng nghìn ô tính Excel hoặc hợp đồng Word 40 trang vào trang Outline sẽ gây vỡ giao diện và biến Wiki thành bãi rác dữ liệu.
- **Giải pháp Triệt để:**
  - Tách bạch rõ 2 tầng:
    - **Tầng Ngữ nghĩa (Outline Wiki Page):** Đóng vai trò là **Executive Summary & Metadata Index**:
      + Header: Tên tài liệu, Mã hợp đồng, Ngày tải lên, Checksum phiên bản.
      + Bảng thông số then chốt (Key Metrics / Highlights): Tóm tắt 5-10 chỉ số quan trọng nhất.
      + Changelog: Lịch sử các lần chỉnh sửa của AI.
      + Liên kết Attachment: Trỏ trực tiếp tới file nhị phân phiên bản mới nhất.
    - **Tầng Nhị phân (Local Storage / Google Drive):** Đóng vai trò là **Source of Truth** chứa file `.docx` / `.xlsx` nguyên gốc có đầy đủ định dạng thẩm mỹ.

---

### 1.4. Cạm bẫy 4: Google Drive Đồng bộ (Native Binary Upload vs Conversion)
- **Vấn đề:** Nếu chuyển đổi (convert) tự động sang Google Docs/Sheets thông qua Google Docs API v1, Google thường làm xô lệch margin, mất font tùy biến và vỡ bảng biểu phức tạp.
- **Giải pháp Triệt để:**
  - Upload file nhị phân trực tiếp bằng **Google Drive API v3 Multipart Upload**:
    - Header: `Content-Type: multipart/related`
    - Metadata: `{"name": "{file_name}", "mimeType": "application/vnd.google-apps.document" (hoặc spreadsheet)}`
    - Content: File nhị phân `.docx` hoặc `.xlsx` đã patch.
  - Google Drive tự động kích hoạt **Office Compatibility Mode**: Người dùng mở ra có thể xem và sửa trực tuyến trên giao diện Google Docs/Sheets mà **không làm thay đổi cấu trúc nhị phân của file gốc**, tải về vẫn là `.docx`/`.xlsx` chuẩn 100%.

---

## 2. KIẾN TRÚC TỔNG THỂ DUAL-STORE PIPELINE

```text
                             [ NGƯỜI DÙNG TẢI FILE ]
                               (DOCX / XLSX / PDF)
                                        │
                                        ▼
                             [ FASTAPI CORE GATEWAY ]
                                        │
            ┌───────────────────────────┴───────────────────────────┐
            ▼                                                       ▼
   [ 1. STORAGE LAYER ]                                    [ 2. EXTRACTION LAYER ]
   Lưu trữ Golden Template                                 Bóc tách ngữ nghĩa nhẹ:
   storage/golden/{file_id}_{name}                         - DOCX: Paragraph & Table Map
   (File nhị phân gốc bất biến)                            - XLSX: Data Cells vs Formulas
                                                           - PDF: Clean text + Markdown table
                                                                    │
                                                                    ▼
                                                           [ 3. OUTLINE SECOND BRAIN ]
                                                           Tạo trang Wiki tóm tắt:
                                                           - Metadata & Key Metrics
                                                           - Link đính kèm Golden Binary
                                                                    │
   [ NGƯỜI DÙNG YÊU CẦU SỬA ] ◄─────────────────────────────────────┘
   (Ví dụ: "Đổi giá ô C5 thành 350k")
            │
            ▼
   [ 4. AI MUTATION AGENT ]
   Phân tích yêu cầu + Context Semantic Map
   Chỉ sinh ra JSON Delta Patch tuân thủ Schema nghiêm ngặt
            │
            ▼
   [ 5. TEMPLATE DELTA PATCHER ]
   - Clone Golden Template -> storage/versions/{file_id}_v2.ext
   - Chạy DocxDeltaPatcher / XlsxDeltaPatcher:
     * Word: Run-span boundary substitution (bảo toàn 100% font/layout)
     * Excel: openpyxl(data_only=False) + fullCalcOnLoad (bảo vệ công thức)
            │
            ▼
   [ 6. DRIVE EGRESS & OUTLINE SYNC ]
   - Google Drive: Upload Multipart Binary (Mở đẹp 100% trên Drive/Docs)
   - Outline Wiki: Cập nhật Changelog v2 + Cập nhật link file mới
```

---

## 3. CHUẨN HÓA SCHEMA JSON DELTA PATCH CỦA AI

Khi người dùng ra lệnh chỉnh sửa tài liệu, AI Gateway bắt buộc phải trả về JSON có cấu trúc sau:

```json
{
  "summary": "Cập nhật đơn giá áo polo và điều chỉnh tên khách hàng",
  "target_file_type": "xlsx",
  "mutations": [
    {
      "type": "cell_update",
      "sheet": "Báo Giá",
      "coordinate": "C2",
      "new_value": 180000,
      "data_type": "number",
      "rationale": "Cập nhật đơn giá áo thun theo thỏa thuận mới"
    },
    {
      "type": "cell_update",
      "sheet": "Báo Giá",
      "coordinate": "B1",
      "new_value": "Công ty TNHH Weamis",
      "data_type": "string",
      "rationale": "Cập nhật tên khách hàng doanh nghiệp"
    }
  ]
}
```

Đối với văn bản Word:
```json
{
  "summary": "Thay đổi thời hạn thực hiện hợp đồng sang 45 ngày",
  "target_file_type": "docx",
  "mutations": [
    {
      "type": "text_replace",
      "target_location": "Điều 4 Khoản 2",
      "find_exact": "trong vòng 30 (ba mươi) ngày",
      "replace_with": "trong vòng 45 (bốn mươi lăm) ngày",
      "rationale": "Gia hạn tiến độ theo yêu cầu của đối tác"
    }
  ]
}
```

