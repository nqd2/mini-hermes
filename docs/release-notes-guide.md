# Hướng dẫn viết WeAI Release Notes (dành cho Agent)

Tài liệu này quy định cách soạn ghi chú phát hành trong [releases.json](../packages/backend/weai/releases/releases.json) để hiển thị cho người dùng cuối trong hộp thoại **WeAI release notes** (**Settings → About** và thông báo cập nhật).

## 1. Đối tượng đọc & nguyên tắc cốt lõi

- **Người đọc là người dùng cuối (end users)** — không phải lập trình viên hay vận hành hạ tầng.
- **Không tóm tắt `git log` nguyên văn.** Hãy dịch thay đổi kỹ thuật thành **lợi ích người dùng nhìn thấy hoặc trải nghiệm được**.
- Mỗi gạch đầu dòng phải trả lời được câu hỏi: *"Người dùng giờ làm được gì mới, hoặc thấy gì tốt/ổn định hơn?"*

## 2. Tiêu chí chọn lọc: Nên đưa vào vs. Nên bỏ qua

### Đưa vào
- Tính năng mới người dùng trực tiếp dùng được (ví dụ: đăng nhập Google, chọn tệp từ Google Drive, xem lịch sử cập nhật).
- Cải tiến rõ rệt về trải nghiệm hàng ngày (chọn model dễ hơn, hạn mức sử dụng công bằng hơn, câu trả lời không bị mất khi mạng chập chờn).
- Sửa lỗi từng ảnh hưởng trực tiếp tới thao tác của người dùng (trình bày bằng hậu quả được khắc phục, không kể tên module hay giao thức).

### Bỏ qua (không viết vào release notes)
- Refactor code, dọn dẹp file, đổi tên biến/symbol, tối ưu build/Docker/CI.
- Chi tiết kỹ thuật nội bộ: `CORS`, `WebSocket`, `OAuth 2.1`, `JWT`, `PostgreSQL`, `Alembic`, `MCP`, `modal`, `API endpoint`, tên biến `.env`.
- Tinh chỉnh giao diện vụn vặt (đổi màu viền, animation loading/splash, chỉnh khoảng cách padding) trừ khi thay đổi cả quy trình sử dụng.
- Cập nhật test, linter, hoặc tài liệu nội bộ.

## 3. Chuyển ngữ Dev-speak → User-benefit

| Tránh viết (Dev-speak) | Nên viết (User-facing) |
| --- | --- |
| `Automatic recovery of missed chat responses after WebSocket reconnects and comma-separated CORS WebSocket origin parsing.` | `More reliable replies that recover automatically if your connection briefly drops during a response.` |
| `Google OAuth sign-in with pending admin approval and MCP HITL confirmation gate.` | `Sign in with your Google account, with administrator approval for new workspace members.` / `Confirmation prompts before any Drive file is changed or deleted.` |
| `Filtered out non-text models and added provider badges in model selector.` | `Clearer model selection showing provider labels up front and only chat-ready models listed.` |
| `Branded animated splash screen, loading indicators, and refined sign-in screen.` | *(Bỏ qua, không cần đưa vào release notes)* |

## 4. Quy chuẩn kỹ thuật của `releases.json`

Kiểm tra thực thi bởi [release_manifest.py](../packages/backend/weai/releases/release_manifest.py):

1. **Plain-text 100%:**
   - Cấm HTML (`<` hoặc `>`).
   - Cấm URL (`http://`, `https://`, `www.`, `mailto:`, `scheme://`).
   - Cấm Markdown link (`](`).
2. **Cấu trúc JSON mỗi phiên bản:**
   - `released_at`: định dạng `"YYYY-MM-DD"`.
   - `sections`: map từ tên mục (`"Added"`, `"Improved"`, `"Fixed"`) sang danh sách chuỗi không rỗng. Chỉ tạo mục nào thực sự có thay đổi đáng chú ý cho người dùng.
3. **Ngôn ngữ & độ dài:**
   - Giữ cùng ngôn ngữ với các bản trước (`0.1.0`–`0.1.3` dùng tiếng Anh ngắn gọn, súc tích).
   - Mỗi phiên bản thường chỉ cần **3–6 gạch đầu dòng** có giá trị nhất; mỗi dòng 1–2 câu ngắn.
4. **Đồng bộ phiên bản khi phát hành:**
   - Cập nhật khớp nhau ở [releases.json](../packages/backend/weai/releases/releases.json), [pyproject.toml](../packages/backend/pyproject.toml), [package.json](../packages/frontend/package.json), và [BuildVersion.svelte](../packages/frontend/src/lib/components/common/BuildVersion.svelte).

## 5. Kiểm tra bắt buộc sau khi sửa

Chạy từ thư mục gốc (hoặc `packages/backend` / `packages/frontend`):

1. Validate manifest:
   ```powershell
   $env:WEAI_RELEASE_VERSION='<version>'
   .\.venv\Scripts\python.exe scripts/check_release_manifest.py --production
   ```
2. Backend release tests (từ `packages/backend`):
   ```powershell
   $env:ENABLE_DB_MIGRATIONS='false'
   $env:AGENT_RUNTIME_SKIP_STARTUP_VALIDATION='true'
   $env:DATABASE_URL='sqlite:///./test-agent-runtime.db'
   ..\..\.venv\Scripts\python.exe -m pytest tests/test_release_manifest.py tests/test_release_routes.py
   ```
3. Frontend release tests (từ `packages/frontend`):
   ```powershell
   node node_modules/vitest/vitest.mjs run src/lib/apis/releases/release.test.ts
   ```
