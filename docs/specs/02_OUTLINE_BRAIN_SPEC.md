# ĐẶC TẢ KỸ THUẬT: SECOND BRAIN TRÊN OUTLINE WIKI

**Mã tài liệu:** SPEC-BRAIN-02  
**Phạm vi:** Tổ chức thư mục đa tenant, API Ingress/Egress, Search và Đồng bộ tri thức nội bộ trên Outline Wiki.  

---

## 1. MỤC TIÊU & TỔ CHỨC CẤU TRÚC ĐA TENANT

### 1.1. Triết lý Thiết kế
Outline Wiki đóng vai trò là **Bộ Não Thứ Hai (Second Brain)** của Mini-Hermes:
- Mọi tài liệu người dùng tải lên, mọi bản thảo AI soạn thảo, và mọi tài liệu đã được phê duyệt đều được lưu trữ trực tiếp trên Outline.
- **Tổ chức ngầm đa tenant:** Mỗi khách hàng/tổ chức được cấp một phân vùng thư mục riêng biệt dưới tiền tố `business/{tenant_id}/`. Không bao giờ để dữ liệu của các tenant lẫn lộn nhau.

### 1.2. Cấu trúc Cây Thư mục Chuẩn (Hierarchical Directory Tree)
```text
Collection: Mini-Hermes Business (Root Collection ID: ef113de4-0a4b-4300-be0e-20f65b8d9ac0)
│
└── 📁 business/
     │
     └── 📁 {tenant_id}/ (Ví dụ: "fnb_chain_01", "weamis_corp", "default_org")
          │
          ├── 📁 01_raw_inputs/
          │    ├── Bang_Bao_Gia_Chi_Tiet.xlsx.md
          │    ├── Mau_Hop_Dong_Mau.docx.md
          │    └── Quy_Dinh_An_Toan.pdf.md
          │
          ├── 📁 02_working_drafts/
          │    ├── Du_Thao_Hop_Dong_Cung_Cap_2026.md
          │    └── Du_Thao_Bien_Ban_Nghiem_Thu.md
          │
          └── 📁 03_approved_deliverables/
               ├── Hop_Dong_Cung_Cap_2026_Approved.md (Gắn URL Google Docs)
               └── Bang_Bao_Gia_Chot_2026_Approved.md (Gắn URL Google Sheets)
```

---

## 2. ĐẶC TẢ CÁC ENDPOINT OUTLINE REST API SỬ DỤNG

Hệ thống giao tiếp với Outline thông qua REST API (sử dụng Header `Authorization: Bearer {OUTLINE_API_KEY}`):

### 2.1. Quản lý Thư mục & Tài liệu:
1. **`POST /api/collections.list`:** Lấy danh sách collection, tìm ID của collection chính `Mini-Hermes Business`.
2. **`POST /api/documents.search`:** Tìm kiếm tài liệu theo từ khóa trong phân vùng của tenant:
   - Payload:
     ```json
     {
       "query": "Hợp đồng dịch vụ F&B",
       "collectionId": "ef113de4-0a4b-4300-be0e-20f65b8d9ac0",
       "limit": 5
     }
     ```
3. **`POST /api/documents.create`:** Tạo mới tài liệu trong thư mục tương ứng:
   - Payload:
     ```json
     {
       "title": "Hop_Dong_Dich_Vu_Weamis",
       "text": "# Hợp đồng Dịch vụ...",
       "collectionId": "ef113de4-0a4b-4300-be0e-20f65b8d9ac0",
       "parentDocumentId": "{folder_id_02_working_drafts}",
       "publish": true
     }
     ```
4. **`POST /api/documents.info`:** Lấy toàn bộ nội dung Markdown chi tiết của một tài liệu theo Document ID.
5. **`POST /api/documents.update`:** Cập nhật nội dung bản thảo khi người dùng yêu cầu chỉnh sửa hoặc gắn thêm đường dẫn Google Docs/Sheets đã xuất.

---

## 3. CƠ CHẾ TỰ ĐỘNG KHỞI TẠO TENANT (AUTO-PROVISIONING)

Khi một request đến với `tenant_id` mới chưa tồn tại trên Outline:
1. `OutlineTenantManager` kiểm tra danh mục xem thư mục `business/{tenant_id}` đã tồn tại chưa.
2. Nếu chưa, tự động tạo cấu trúc 3 tầng:
   - Tạo document cha: `business/{tenant_id}`
   - Tạo 3 documents con: `01_raw_inputs`, `02_working_drafts`, `03_approved_deliverables`
3. Cache lại các ID này trong bộ nhớ để không phải query nhiều lần.

---

## 4. CƠ CHẾ XÁC THỰC & ĐĂNG NHẬP 1-CHẠM (ONE-CLICK AUTH)

Để quản trị viên hoặc người dùng có thể mở Outline Wiki từ trình duyệt mà không bị vướng màn hình đăng nhập hay cấu hình Google Cloud Console phức tạp:
1. **Endpoint Gateway:** `GET http://localhost:8000/api/auth/outline/login`
2. **Cơ chế hoạt động:**
   - Gateway sinh ra một JWT Session Token dài hạn (1 năm) gán cho tài khoản Admin `kienvip2501@gmail.com`.
   - Trả về mã phản hồi `302 Redirect` tới `http://localhost:3030/` kèm header:
     `Set-Cookie: accessToken={JWT_SESSION_TOKEN}; Path=/; Max-Age=31536000; SameSite=Lax`
   - Trình duyệt tự động nhận cookie và mở thẳng giao diện quản trị Outline Wiki với đầy đủ tài liệu và quyền Admin.

