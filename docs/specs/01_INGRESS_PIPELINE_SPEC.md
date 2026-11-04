# ĐẶC TẢ KỸ THUẬT: INGRESS PIPELINE CHO DOCX, XLSX, PDF

**Mã tài liệu:** SPEC-INGRESS-01  
**Phạm vi:** Trích xuất, chuẩn hóa và chuyển đổi tài liệu đầu vào sang Clean Markdown trong Mini-Hermes MVP.  

---

## 1. MỤC TIÊU & NGUYÊN TẮC THIẾT KẾ
Ingress Pipeline có nhiệm vụ tiếp nhận các file tài liệu doanh nghiệp thô do người dùng tải lên thông qua Open WebUI hoặc API Gateway, sau đó phân tích cú pháp để tạo ra **Clean Markdown**:
- Không phụ thuộc vào các dịch vụ chuyển đổi ngoài tốn phí hay cồng kềnh.
- Sử dụng trực tiếp các thư viện Python thuần (`python-docx`, `openpyxl`, `pypdf`/`pdfplumber`).
- Đầu ra là Markdown sạch, giữ nguyên cấu trúc phân cấp (headings, tables, lists) để LLM đọc hiểu ngữ cảnh tối đa và đẩy thẳng lên Outline Wiki `01_raw_inputs`.

---

## 2. ĐẶC TẢ BÓC TÁCH FILE DOCX (MICROSOFT WORD)

### 2.1. Thư viện sử dụng: `python-docx`
### 2.2. Quy trình xử lý (Step-by-Step Flow):
```mermaid
flowchart TD
    FILE[File .docx Uploaded] --> VALIDATE[Kiểm tra tính hợp lệ của file XML/ZIP]
    VALIDATE --> ITER_BLOCKS[Duyệt tuần tự các khối Block trong Document]
    ITER_BLOCKS --> CHECK_TYPE{Loại khối Block?}
    
    CHECK_TYPE -->|Paragraph| PARSE_P[Phân tích Heading & Text Run]
    PARSE_P --> MAP_HEADING[Mapping Style: Heading 1..3 -> #, ##, ###]
    MAP_HEADING --> MAP_INLINE[Định dạng in đậm **, in nghiêng *, danh sách -]
    
    CHECK_TYPE -->|Table| PARSE_T[Phân tích Bảng]
    PARSE_T --> EXTRACT_CELLS[Trích xuất từng ô & khử ký tự \n trong ô]
    EXTRACT_CELLS --> BUILD_MD_TABLE[Tạo Markdown Table Header & Rows]
    
    MAP_INLINE --> MERGE[Gộp vào chuỗi Markdown hoàn chỉnh]
    BUILD_MD_TABLE --> MERGE
    MERGE --> CLEANUP[Khử khoảng trắng dư & chuẩn hóa xuống dòng]
    CLEANUP --> OUTPUT[Ghi vào Outline: 01_raw_inputs/{filename}.md]
```

### 2.3. Quy tắc Ánh xạ Phần tử (Element Mapping Rules):
| Phần tử DOCX | Kiểu Style trong `python-docx` | Định dạng Markdown Đầu ra |
| :--- | :--- | :--- |
| **Title** | `p.style.name.startswith('Title')` | `# [Nội dung Title]` |
| **Heading 1** | `p.style.name.startswith('Heading 1')` | `# [Nội dung Heading 1]` |
| **Heading 2** | `p.style.name.startswith('Heading 2')` | `## [Nội dung Heading 2]` |
| **Heading 3** | `p.style.name.startswith('Heading 3')` | `### [Nội dung Heading 3]` |
| **List Bullet** | `p.style.name.startswith('List Bullet')` | `- [Nội dung Item]` |
| **List Number** | `p.style.name.startswith('List Number')` | `1. [Nội dung Item]` |
| **Bảng Table** | `table.rows` | `| Cột 1 | Cột 2 \|`<br>`| --- | --- \|`<br>`| Dữ liệu 1 | Dữ liệu 2 \|` |

---

## 3. ĐẶC TẢ BÓC TÁCH FILE XLSX (MICROSOFT EXCEL)

### 3.1. Thư viện sử dụng: `openpyxl` (chế độ `data_only=True` để lấy giá trị tính toán thay vì chuỗi công thức).
### 3.2. Quy trình xử lý:
1. **Lấy danh sách Sheet:** Lặp qua từng worksheet trong workbook. Bỏ qua các sheet ẩn (`ws.sheet_state != 'visible'`).
2. **Tìm Vùng dữ liệu Hữu hiệu (Bounding Box):**
   - Không đọc toàn bộ lưới vô hạn; tìm `min_row`, `max_row`, `min_column`, `max_column` thực tế có dữ liệu.
3. **Format Dữ liệu từng Ô (Cell Formatter):**
   - **Số thực / Tiền tệ:** Giữ nguyên độ chính xác 2 chữ số thập phân hoặc format tiền tệ chuẩn (ví dụ: `15,000,000 VND`).
   - **Ngày tháng:** Chuyển đổi đối tượng `datetime` sang định dạng `YYYY-MM-DD` hoặc `DD/MM/YYYY`.
   - **Ô trống:** Điển chuỗi rỗng ` ` hoặc `-` để không làm lệch cột Markdown.
4. **Cơ chế Chống Tràn Token (Token Overflow Safeguard):**
   - Nếu một sheet có trên 50 hàng: Trích xuất 20 hàng đầu tiên + chèn chú thích `... (Đã rút gọn [N] hàng) ...` + sinh khối tổng kết metadata (Tổng số dòng, số cột, tổng các cột số) để LLM nắm được bức tranh toàn cảnh mà không gây quá tải context.

---

## 4. ĐẶC TẢ BÓC TÁCH FILE PDF

### 4.1. Thư viện sử dụng: `pypdf` kết hợp fallback regex parsing.
### 4.2. Quy trình xử lý:
1. **Bóc tách Từng Trang (Page-by-Page Extraction):** Duyệt qua các trang và trích xuất chuỗi văn bản.
2. **Lọc Nhiễu Header & Footer (De-noising):**
   - Nhận diện các dòng lặp lại liên tục ở đầu hoặc cuối trang (ví dụ: `Trang X/Y`, `Bản quyền thuộc công ty...`, `CONFIDENTIAL`).
   - Tự động loại bỏ các dòng này khỏi chuỗi văn bản trích xuất.
3. **Gắn kết Đoạn văn (Paragraph Reconstruction):**
   - PDF thường tự động chèn ký tự `\n` ở cuối mỗi dòng hiển thị.
   - Thuật toán kiểm tra ký tự kết thúc dòng: Nếu không phải là dấu chấm `.`, hai chấm `:` hoặc chấm than/hỏi `!/?`, thực hiện nối dòng với dòng tiếp theo bằng một dấu cách thay vì ngắt đoạn.
4. **Nhận diện Tiêu đề:**
   - Dựa vào các từ khóa phân đoạn (`I.`, `II.`, `1.`, `2.`, `Điều 1:`, `Chương II:`) hoặc chữ viết hoa toàn bộ để tự động phong thành Heading Markdown.

---

## 5. QUY CHUẨN LƯU TRỮ VÀO OUTLINE WIKI
Sau khi bóc tách hoàn tất:
1. File Markdown được gán metadata ở đầu file (YAML Frontmatter):
   ```markdown
   ---
   source_filename: Bang_Bao_Gia_Dong_Phuc_Weamis.xlsx
   source_type: xlsx
   ingested_at: 2026-11-03T18:00:00Z
   tenant_id: default_org
   status: raw_input
   ---
   ```
2. Gọi API Outline `documents.create` đẩy vào thư mục:
   `business/{tenant_id}/01_raw_inputs/{source_filename}.md`
3. Trả về cho hệ thống Gateway Document ID trên Outline để sẵn sàng phục vụ tra cứu.

