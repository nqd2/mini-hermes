# Mini-Hermes V2 — Master Roadmap

**Trạng thái: Approved as planning baseline — implementation contracts pending.**

V2 sử dụng database, object-storage namespace và index riêng; không migrate dữ liệu, revision, conversation, approval hoặc checkpoint V1. Không xóa hoặc thay đổi dữ liệu production V1. File cần dùng trong V2 được upload lại.

Giữ AgentRuntime hiện tại làm production execution path duy nhất và phát triển tiếp implementation bên trong theo ADR-001/002. Open WebUI tiếp tục sở hữu UI, authentication, RBAC, chat và model management. Public contracts mà Open WebUI sử dụng được bảo toàn, trừ thay đổi versioned có phê duyệt riêng.

V2-R1 hỗ trợ XLSX, DOCX, PPTX và PDF; PDF chỉ đọc. XLSX là định dạng đầu tiên có structured workbook model. Không đưa Ollama, OCR, Office legacy hoặc OpenDocument vào phạm vi bắt buộc.

## Phase 0 — Khóa kiến trúc và chuẩn bị triển khai

Phase 0 tạo và review chín deliverable:

1. **ADR-V2:** ranh giới greenfield, isolation dữ liệu và tính liên tục của AgentRuntime.
2. **Architecture spec:** module, dependency, service ownership và luồng dữ liệu.
3. **Contract catalog:** API, tool, event, error, cursor và idempotency contracts.
4. **Data model spec:** PostgreSQL entities, invariants, revision publication và index-build lifecycle.
5. **Coverage semantics:** điều kiện và evidence cho parsing, indexing, retrieval, analysis, summary và LLM context.
6. **Format capability matrix:** chức năng đọc, inspect, search, export và **allowlist chỉnh sửa cụ thể** cho XLSX, DOCX, PPTX; PDF chỉ đọc. Các thao tác ngoài allowlist phải bị từ chối rõ ràng.
7. **Compatibility manifest:** inventory frontend-facing APIs, SSE events, authentication/session và error contracts đang được Open WebUI sử dụng; ghi consumer và cách kiểm thử từng contract.
8. **Test plan:** fixtures, characterization tests, contract tests và evidence cần có cho từng phase.
9. **Agent work breakdown:** dependency graph, owner, phạm vi file/module và acceptance criteria cho từng task.

Khóa các contract cốt lõi trước Phase 1. Fixture và test infrastructure độc lập có thể chuẩn bị song song, nhưng không tự định nghĩa interface khác catalog. Mỗi module dùng chung có một owner; thay đổi interface phải được review và cập nhật contract tests trước khi tích hợp. Workstream tích hợp chịu trách nhiệm kiểm tra các boundary và giải quyết thay đổi giao nhau.

Viết characterization tests cho AgentRuntime hiện tại: start, replay, cancel, resume, approval, tool ledger và binding. Chuẩn bị fixture XLSX nhiều sheet, multiline, ô lớn, merged cells và formula có/không có cached value. Dùng workbook 57.765 ký tự nếu có fixture gốc; nếu không, dùng fixture tổng hợp tương đương và ghi rõ giới hạn chứng cứ.

**Gate:** Chín deliverable được review; contracts và ownership được khóa; characterization tests và test fixtures sẵn sàng. Sau gate này, tách từng phase thành coding task có thể giao độc lập.

## Phase 1 — Persistence và môi trường V2

- Khởi tạo PostgreSQL/SQLAlchemy async schema, object-storage namespace và index namespace riêng. Startup fail closed nếu V2 được cấu hình trỏ nhầm database hoặc storage V1.
- Thiết lập fresh bootstrap, migrations V2, FK, transaction boundaries, outbox và backup/restore. Không tạo adapter chỉ để đọc persisted data V1.
- Giữ import/export theo định dạng V2 có version; import không khôi phục approval hoặc privileged execution state.

**Gate:** Fresh bootstrap, isolation và backup/restore được kiểm tra trên PostgreSQL disposable; V2 không đọc nhầm dữ liệu V1.

## Phase 2 — Document Engine và ingestion

- Upload xác thực, validate định dạng và giới hạn tài nguyên, lưu immutable bytes có checksum, tạo revision và ingestion job idempotent. Worker parse, index, kiểm tra integrity rồi mới publish active index.
- XLSX canonical model bảo toàn workbook, sheet/order/visibility, row, cell coordinate/type/raw value, formula expression, cached value/cache status và merged range/anchor. Text render là representation phục vụ truy xuất, không phải nguồn chuẩn.
- Chunk theo sheet và row; giữ cell cùng hàng khi vừa giới hạn. Row hoặc cell quá lớn được chia với anchor và offset để ghép lại không mất hoặc trùng dữ liệu.
- Index build gắn document, revision, source checksum và parser/chunker/schema versions. Build mới được verify trước khi active pointer chuyển bằng transaction; build cũ được giữ theo retention policy V2.
- Thực hiện DOCX, PPTX và PDF theo capability matrix đã khóa. Parsing completeness phải dựa trên evidence và tiêu chí riêng của từng định dạng. Với PDF, hoàn tất trích xuất text layer không chứng minh nội dung hình ảnh đã được hiểu.

Dựng vertical slice đầu tiên bằng một workbook và một tác vụ đọc:

**Open WebUI → AgentRuntime hiện tại → Document API V2 → XLSX Engine → tool result → UI.**

Slice dùng contract V2 và được kiểm thử cùng các boundary thật; không chờ hoàn tất toàn bộ Document Engine hay Phase 4.

**Gate:** Kiểm tra anchors, completeness theo định dạng, ingestion restart, revision/build isolation và active-index publication. Vertical slice chạy qua các boundary với fixture được xác định.

## Phase 3 — Document tools, intent và coverage

- Cung cấp `inspect_document`, `read_document_range`, `read_spreadsheet_range`, `search_document`, `execute_document_analysis`, `start_document_summary`, `get_document_task`, `export_document` và `propose_document_edit` theo contract catalog.
- Read/search không âm thầm tạo hoặc retry index job. Nếu index chưa sẵn sàng, trả pending hoặc `INDEX_NOT_READY` cùng job ID hiện có.
- Tính page size theo toàn bộ JSON response đã serialize, bao gồm metadata và escaping. Ưu tiên ranh giới cell; cell quá lớn có reference và đường đọc tiếp. Cursor gắn revision/index build và phạm vi được phép; mỗi lần sử dụng lại kiểm tra quyền hiện tại. Nếu envelope tối thiểu không vừa budget, trả lỗi rõ ràng.
- Router phân biệt `INSPECT_AND_OVERVIEW`, `PREPARE_FOR_QA`, `FULL_READ`, `SUMMARIZE`, `ANALYZE`, `SEARCH/FILTER` và `EDIT_WITH_APPROVAL`. Yêu cầu chung “đọc file Excel này” mặc định inspect mọi sheet, lấy mẫu có hệ thống và báo phạm vi thực tế; không tự nhận đã đọc nguyên văn hoặc phân tích toàn bộ.
- Theo dõi riêng `index_coverage`, `retrieval_coverage`, `analysis_coverage`, `summary_coverage` và `answer_evidence`. Coverage các trang chỉ hoàn chỉnh khi các khoảng hợp lệ liên tục, cùng revision/build, không gap.
- Mở rộng vertical slice Phase 2 để kiểm tra output budget, cursor, evidence và hiển thị trạng thái coverage từ tool đến UI.

Các trạng thái sau độc lập và có evidence riêng:

```text
PARSER_COMPLETE ≠ INDEX_COMPLETE ≠ ANALYSIS_COMPLETE ≠ LLM_CONTEXT_COMPLETE
```

**Gate:** Contract tests chứng minh budget, pagination, provenance, authorization, intent routing và answer gate; không có coverage claim vượt evidence.

## Phase 4 — AgentRuntime, MCP và Skill Runtime

- Replatform từng thành phần AgentRuntime hiện tại sang persistence V2; giữ ngữ nghĩa `start`, `stream(after_sequence)`, `cancel`, `resume` và `get_status`. V2 từ chối checkpoint V1 hoặc checkpoint không khớp engine/run binding.
- Persist event trước khi publish; replay theo sequence, phát hiện gap và phục hồi bằng status/snapshot cùng replay. Giữ durable invocation ledger, idempotency, cancellation, recovery và agent approval riêng với document approval.
- Phân biệt **internal idempotency** với bảo đảm của external provider. Tool chỉ được xếp retry-safe sau khi xác minh toàn bộ đường gọi. Với read-only tool V2, retry tối đa ba lần cho lỗi kết nối tạm thời bằng exponential backoff; không retry lỗi quyền, validation hoặc lỗi vĩnh viễn.
- Nếu external outcome không rõ và provider không hỗ trợ idempotency hoặc truy vấn trạng thái phù hợp, quarantine để reconciliation. Không tuyên bố exactly-once chỉ dựa vào local idempotency key.
- MCP Gateway tiếp tục sở hữu credentials, isolation, timeout, rate limit và audit; Skill Runtime sở hữu immutable versions, permissions và sandbox execution. Runtime không ghi trực tiếp document database hoặc object storage.
- Giữ giới hạn 95.000 ký tự khi chuẩn bị request và 100.000 ký tự cho JSON cuối sau provider transformations; theo dõi riêng ký tự, UTF-8 bytes và token.

**Gate:** Characterization và contract tests pass; executable tests bao gồm duplicate invocation, provider có/không có idempotency, unknown outcome, restart, replay/gap, cancel/resume, stale checkpoint và request bị từ chối trước provider call khi quá giới hạn.

## Phase 5 — Editing và tích hợp Open WebUI

- Tích hợp tool V2 và trạng thái ingestion/coverage vào UI theo compatibility manifest. Thay đổi public contract chỉ thực hiện qua versioned change được duyệt và cập nhật mọi consumer liên quan.
- Chỉ hỗ trợ thao tác nằm trong format capability matrix. Mọi mutation đi qua Revision Service: preview, approval tường minh, kiểm tra quyền hiện tại, base revision và canonical patch hash. Approval hết hạn sau 15 phút từ preview; retry không gia hạn.
- Quản lý publication theo trạng thái `STAGED → VERIFIED → COMMITTED → AVAILABLE`. Xác minh immutable staged bytes trước; transaction DB tiêu thụ approval, ghi revision/outbox và cập nhật current pointer là ranh giới publication.
- Object đã stage nhưng chưa commit được thu hồi sau safety interval; committed revision thiếu object hoặc sai checksum phải fail closed và phát cảnh báo. Retry cùng idempotency key trả cùng revision; rollback tạo revision bù và giữ lịch sử.

**Gate:** UI integration và các test revoked permission, expiry, patch mismatch, concurrent edit, commit retry, crash ở từng publication boundary và rollback pass.

## Phase 6 — Staging và quyết định release

- Chạy E2E upload → tool → runtime → UI cho bốn định dạng; bao gồm workbook lớn, budget 5.952 ký tự, multiline, formula, merged cells, lỗi kết nối giữa các trang và partial coverage.
- Chạy security isolation, recovery/replay, concurrency, resource exhaustion, backup/restore và load trên staging. Đo baseline trước khi khóa ngưỡng hiệu năng tuyệt đối.
- Các gate bắt buộc: không có truy cập hoặc commit trái quyền, không duplicate external side effects và không mất committed revision trong các tình huống được kiểm thử.
- Production migration, reset, canary và cutover cần quyết định và phê duyệt riêng sau staging. Conversation V1 được drain hoặc xử lý theo kế hoạch ngừng V1 riêng; không chuyển checkpoint sang V2.

## Kiểm thử và bàn giao

Mỗi coding task có test hồi quy, owner, dependency và acceptance criteria. Chạy backend tests liên quan bằng `python -m pytest tests/<files>` từ `packages/backend`; chạy frontend Vitest theo hướng dẫn repository. PostgreSQL integration và migration tests chỉ dùng `MH_TEST_POSTGRES_URL` trỏ tới database disposable. Chạy `git diff --check` trước khi báo hoàn tất và nêu rõ các gate chưa xác minh.

Master roadmap này xác lập phạm vi và thứ tự, **chưa phải tập coding task sẵn giao**. Kết quả cần thực hiện tiếp theo là bộ deliverable Phase 0; sau khi khóa contracts, từng phase được chia thành task đủ nhỏ để triển khai và review độc lập.
