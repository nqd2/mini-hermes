# ĐẶC TẢ KỸ THUẬT: TƯƠNG TÁC 100% NATIVE TRÊN OPEN WEBUI

**Mã tài liệu:** SPEC-OPENWEBUI-03  
**Phạm vi:** Tích hợp Open WebUI thông qua giao thức OpenAI API chuẩn, Action Tool Plugins và Suggestion Chips Native.  

---

## 1. NGUYÊN TẮC BẤT DI BẤT DỊCH
1. **Không Tiêm Mã DOM Tùy Biến (Zero Injected Custom DOM):**
   - Không can thiệp sửa đổi cấu trúc HTML của Open WebUI.
   - Không tạo side-panel/drawer cố định bằng JavaScript ngoài.
   - Không tiêm floating button hay modal tự chế.
2. **Khai thác Tối đa Giao diện Chuẩn của Open WebUI:**
   - Dùng **Native Follow-up Suggestion Chips** (`follow_ups` trong payload OpenAI completion).
   - Dùng **Native Action Tool Plugin** (`hermes_sync_action.py`) nằm gọn gàng trong thanh công cụ bên dưới mỗi tin nhắn.
   - Dùng **Markdown Link Native** để người dùng bấm trực tiếp vào tài liệu Google Docs hoặc Outline vừa sinh ra.

---

## 2. ĐẶC TẢ CÁC THÀNH PHẦN NATIVE

### 2.1. Native Suggestion Chips (`follow_ups`)
Khi Mini-Hermes hoàn thành một lượt phản hồi chứa nội dung bản thảo hoặc bảng biểu:
- Endpoint `POST /v1/chat/completions` tự động đính kèm trường `follow_ups` vào response payload (theo chuẩn Open WebUI):
  ```json
  {
    "id": "chatcmpl-hermes-123",
    "object": "chat.completion",
    "created": 1793700000,
    "model": "mini-hermes-native",
    "choices": [
      {
        "index": 0,
        "message": {
          "role": "assistant",
          "content": "Dưới đây là Dự thảo Hợp đồng..."
        },
        "finish_reason": "stop"
      }
    ],
    "follow_ups": [
      "🚀 Phê duyệt & Xuất sang Google Docs",
      "📊 Xuất sang Google Sheets",
      "✏️ Chỉnh sửa điều khoản thanh toán"
    ]
  }
  ```
- **Hành vi trên Giao diện:** Open WebUI tự động render các chip bấm dạng pill dưới chân tin nhắn. Khi người dùng nhấp vào chip `🚀 Phê duyệt & Xuất sang Google Docs`, nội dung này được gửi tự động vào ô chat như một tin nhắn của người dùng.

### 2.2. Bộ Nhận diện Ý định Duyệt (Approval Intent Classifier)
Tại Gateway `openai_v1_routes.py`, hệ thống kiểm tra câu lệnh người dùng bằng hàm phân tích mẫu câu (Pattern Matching):
- Các cụm từ kích hoạt phê duyệt:
  - `"🚀 phê duyệt"`
  - `"phê duyệt & xuất sang google docs"`
  - `"xuất docs"`, `"xuất google docs"`
  - `"xuất sheets"`, `"xuất google sheets"`
  - `"đồng ý xuất bản"`, `"chốt hợp đồng"`
- Khi phát hiện ý định duyệt:
  1. Gateway truy vấn bản thảo gần nhất trong `02_working_drafts` của tenant.
  2. Kích hoạt luồng Egress Engine chuyển đổi sang Google Docs hoặc Google Sheets.
  3. Trả về phản hồi chúc mừng kèm link clickable mở trực tiếp Google Docs / Sheets.

### 2.3. Native Action Tool (`hermes_sync_action.py`)
Ngoài Suggestion Chips, hệ thống cung cấp một Action Tool đăng ký trong Open WebUI:
- Vị trí: Biểu tượng nút bấm Action ở menu thao tác tin nhắn (bên cạnh nút Copy, Thumbs Up).
- Chức năng: Cho phép người dùng bấm duyệt tức thì bất kỳ tin nhắn nào mà không cần gửi thêm lượt chat mới.
- Mã nguồn action gửi request ngầm đến `POST http://api-serverless:8000/api/v1/sync/outline-to-gdocs` và stream trạng thái cập nhật lại tin nhắn.

