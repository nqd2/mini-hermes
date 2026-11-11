# TÀI LIỆU HỆ THỐNG MINI-HERMES

Các bản spec dưới đây ghi lại những phương án từng được nghiên cứu. Kiến trúc và lệnh chạy hiện hành nằm trong [README ở thư mục gốc](../README.md). Những mô tả về Outline và luồng xuất HTML trong các spec cũ không còn là cấu hình triển khai.

## Đặc tả production hiện hành

- [Đặc tả sử dụng, thiết kế kỹ thuật, kiểm thử và audit Mini-Hermes](design/mini-hermes-use-cases-design-and-tests.md): toàn bộ giao diện sản phẩm; expected outputs; persistence mới cho cả Open WebUI; không legacy compatibility và không Ollama. Test scenarios trong tài liệu chưa là kết quả đã chạy.
- [ADR-001](adr/ADR-001-agent-runtime-production-path.md), [ADR-002](adr/ADR-002-agent-runtime-contract-domain-boundaries.md) và [implementation plan](plans/production-agent-implementation-plan.md) xác định execution path, ownership và production gates.
- Baseline production là 10–20 người dùng duy nhất trong rolling 30-day window; đây không phải giới hạn concurrent users. Availability 99,9% là mục tiêu nội bộ, chưa phải SLA. Quyền reset DB local khi triển khai không áp dụng production. Lịch sử kết quả kiểm thử trong progress ledger được giữ nguyên.

---

## Tài liệu tham khảo lịch sử

1. **[Tài liệu Đặc tả Yêu cầu Phần mềm (SRS - Baseline)](./SRS_MINI_HERMES_MVP.md)**
   *Mô tả toàn diện phạm vi MVP, Document-Only Sandbox, các Use Case nghiệp vụ, kiến trúc luồng Ingress/Egress và tiêu chuẩn nghiệm thu.*

2. **[SPEC 01: Ingress Pipeline Specification](./specs/01_INGRESS_PIPELINE_SPEC.md)**
   *Đặc tả bóc tách file DOCX, XLSX, PDF sang Clean Markdown không phụ thuộc dịch vụ ngoài.*

3. **[SPEC 02: Second Brain (Outline Wiki) Specification](./specs/02_OUTLINE_BRAIN_SPEC.md)**
   *Đặc tả cấu trúc thư mục đa tenant ngầm `business/{tenant_id}/(01_raw_inputs, 02_working_drafts, 03_approved_deliverables)` và cơ chế đăng nhập 1-chạm.*

4. **[SPEC 03: Native Open WebUI Interaction Specification](./specs/03_OPENWEBUI_NATIVE_SPEC.md)**
   *Đặc tả tích hợp 100% Native Open WebUI: Follow-up Suggestion Chips, Native Action Tool, và bộ nhận diện ý định phê duyệt (Approval Intent Classifier).*

5. **[SPEC 04: Google Docs & Sheets Egress Specification](./specs/04_EGRESS_GDOCS_GSHEETS_SPEC.md)**
   *Đặc tả Google Drive API v3 Multipart HTML Upload, bộ lọc Clean HTML Normalizer loại bỏ rác HTML, và định dạng typography doanh nghiệp thẩm mỹ cao.*

6. **[SPEC 05: Template-Preserving Delta Architecture](./specs/05_TEMPLATE_PRESERVING_DELTA_ARCHITECTURE.md)**
   *Đặc tả Dual-Store Pipeline, OpenXML Run-Span Preservation và Full-Recalculation Guardrails.*

7. **[SPEC 06: Office Assistant V2 Generalized Specification](./specs/06_OFFICE_ASSISTANT_V2_GENERALIZED.md)**
   *Đặc tả Context-First Gate, Schema-Driven LLM Mutation, 4 Nguyên tắc Bất biến (Zero Mock Data, Zero Regex Routing, Fail-Fast, No Fake Files).*

8. **[SPEC 07: Enterprise Document Copilot Architecture](./specs/07_ENTERPRISE_DOCUMENT_COPILOT_ARCHITECTURE.md)**
   *Đặc tả 10 Trụ cột Kỹ thuật Nâng cao: Two-Phase Commit Preview, In-Memory Buffer, Multi-Turn Context Accumulator, Formula Integrity Firewall, và Anchor Map hai chặng.*


---

## 🚀 TRẠNG THÁI HIỆN TẠI (CURRENT SYSTEM STATUS)
- **Ứng dụng:** `packages/frontend` và `packages/backend` dựa trên Open WebUI; bản upstream nằm trong `references/open-webui`.
- **Dữ liệu:** PostgreSQL lưu metadata, revisions, previews, OAuth và sync jobs; SeaweedFS lưu nội dung file. Redis phục vụ Open WebUI.
- **Triển khai:** `compose.yaml` dùng một image ứng dụng cho app, worker và Google connector. App nghe tại `http://localhost:8080`; S3 dev tại `http://127.0.0.1:8333`. Outline đã ra khỏi stack.
- **Luồng chỉnh sửa:** Agent tạo đề xuất, người dùng xác nhận preview; commit tạo revision nội bộ và job tự đồng bộ Google Drive. Xem [README chính](../README.md) để chạy local hoặc Compose.

