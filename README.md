# AIVN Excel QA

Giải pháp cho đề thi AI2026, Chủ đề 2 "Hỏi đáp trên bảng tính Excel": trả lời câu hỏi ngôn ngữ tự nhiên (tiếng Anh, Việt, Hàn, Trung…) trên các file `.xlsx` có header nhiều tầng, ô gộp, nhiều bảng hoặc nhiều sheet. LLM dùng **Qwen3.7 Flash qua OpenRouter**, đúng yêu cầu của đề.

## Yêu cầu
- Python **3.11** (đã chạy với 3.11.5), hệ điều hành **Linux**. Phần thực thi dùng `multiprocessing` kiểu `fork`.
- Một khóa API OpenRouter.

## Cài đặt
```bash
git clone <repo-url> aivn-excel-qa
cd aivn-excel-qa
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # rồi điền OPENROUTER_API_KEY vào .env
```
Có thể đặt khóa bằng biến môi trường thay cho `.env`: `export OPENROUTER_API_KEY=...`. Không đưa khóa vào mã nguồn, và không commit file `.env`.

## Chạy
```bash
python run.py --data-dir "<thư mục chứa xlsx_files/ và queries.json>" --out submission.csv
```
Thư mục dữ liệu có cùng cấu trúc với bộ dữ liệu BTC công bố:
```
<data-dir>/
├── xlsx_files/        # các file .xlsx
└── queries.json       # [{"id": ..., "file_name": "...", "query": "..."}]
```
- `file_name` có thể là một tên file hoặc một danh sách tên file (câu hỏi trên nhiều file).
- Kết quả là `submission.csv` gồm hai cột `id,answer`, mỗi `id` đúng một dòng. Nếu một câu không giải được thì vẫn có dòng với ô `answer` để trống, nên file luôn hợp lệ.

Tham số dòng lệnh:

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `--data-dir` | (bắt buộc) | Thư mục dữ liệu như trên |
| `--out` | `submission.csv` | File kết quả |
| `--workers` | `6` | Số câu hỏi xử lý song song |
| `--ids` | – | Chỉ chạy các id liệt kê, cách nhau bởi dấu phẩy (để debug) |
| `--trace` | – | Ghi file JSONL: các ứng viên, đường quyết định, token, độ trễ của từng câu |

Biến môi trường (hoặc `.env`):

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `OPENROUTER_API_KEY` | – | Bắt buộc |
| `QA_MODEL` | `qwen/qwen3.7-flash` | Model trên OpenRouter |
| `QA_TIME_BUDGET` | 96 giây × số câu | Ngân sách thời gian cho cả lượt chạy; quá 85% ngân sách thì không leo thang nữa |
| `QA_CALL_TIMEOUT` | `90` | Timeout mỗi lần gọi API (giây) |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Endpoint |

Chấm điểm Exact Match khi có đáp án:
```bash
python evaluate.py submission.csv <đáp_án.csv>      # đáp án: CSV có hai cột id,answer
```

## Kiến trúc
Sơ đồ: `docs/architecture.drawio` (mở bằng app.diagrams.net hoặc extension Draw.io Integration của VS Code). Trang 1 là luồng xử lý, trang 2 là tech stack.

**Phần A – mỗi workbook, chạy một lần, không gọi LLM** (`excel_parser.py`, `engine/ir.py`)
- Đọc ô bằng openpyxl: lấy giá trị cache của công thức, tự tính khi không có cache; xử lý ô gộp, số/ngày dạng text, ký hiệu thiếu dữ liệu.
- Tách vùng bảng (bảng xếp chồng, bảng cạnh nhau, biểu mẫu key–value) và dựng header nhiều tầng, có ghép các header bị xuống dòng.
- Dựng cây hàng cha › con từ nhiều tín hiệu: thụt lề, cột Indent Level, hàng tiêu đề nhóm, nhãn khoảng lồng nhau, tổng nhất quán (cha = Σ con), khối nhãn lặp lại, nhãn thời gian (năm/quý). Đánh dấu dòng tổng hợp.
- Kết quả là **TableIR**: mỗi giá trị kèm địa chỉ ô A1.

**Phần B – mỗi câu hỏi** (`engine/v3.py`)
- Mỗi bảng được đưa cho LLM dưới dạng **pandas DataFrame** `T0, T1, …` với các cột `row`, `path`, `level_k`, `is_summary`, `has_children` và tên cột giá trị đọc được, kèm bảng dài `facts`.
- LLM cũng nhận một **bản đồ** của workbook. Mỗi cột trong bản đồ có chú thích ngữ nghĩa: rate, average, count, code hay year; có cộng dồn được không; đơn vị gì. Text đáng ngờ trong ô được đánh dấu để chống prompt injection.
- **Bước 1** chạy song song và tắt reasoning:
  - P1: LLM viết chương trình pandas, chạy trong tiến trình con có timeout.
  - P2: LLM đọc trực tiếp bản đồ.
  
  Nếu hai đáp án (sau khi định dạng) trùng nhau và có căn cứ trong dữ liệu thì trả lời luôn.
- **Bước 2**, chỉ khi bất đồng: chạy thêm P1 ×2 và P2 với reasoning `low`, bỏ phiếu trên mọi ứng viên hợp lệ. Hòa phiếu thì gọi trọng tài.
- **Định dạng đáp án** theo đặc tả do LLM trả về (kiểu, số chữ số thập phân, dấu phân cách). Có regex dự phòng cho câu hỏi tiếng Anh, Việt, Hàn, Trung, Nhật. Làm tròn half-up bằng `Decimal`.

## Kết quả đo được (Qwen3.7 Flash, mỗi bộ chạy 1 lần)
| Bộ dữ liệu | Exact Match |
|---|---|
| Public 2 của BTC (30 câu) – dùng để debug, kết quả lạc quan | 30/30 |
| Public 1 của BTC (20 câu) | 16/20 |
| Bộ robustness tự xây (26 câu, chưa dùng để chỉnh) | 26/26 |
| Bộ nhiều file tự xây (37 câu, chưa dùng để chỉnh) | 34/37 |

Trên public 2, chạy 30 câu với 6 luồng:
- Thời gian 3 phút 50 giây; độ trễ trung vị khoảng 10 giây/câu.
- Khoảng 17k token/câu, tức khoảng $0.001/câu theo giá OpenRouter (giới hạn $0.10).
- RAM đỉnh khoảng 190 MB.

## Cấu trúc thư mục
```
run.py            điểm vào dòng lệnh
llm.py            gọi Qwen3.7 Flash qua OpenRouter (cấu hình từ biến môi trường / .env)
excel_parser.py   đọc và khôi phục cấu trúc bảng
formatting.py     định dạng đáp án
engine/ir.py      TableIR và bản đồ
engine/v3.py      DataFrame cho chương trình, lớp ngữ nghĩa cột, ứng viên, bỏ phiếu, trọng tài
evaluate.py       chấm Exact Match
docs/             sơ đồ kiến trúc (draw.io)
```

## Giới hạn đã biết
- Code do LLM sinh chạy bằng `exec()` trong tiến trình con. Đây là cách cô lập lỗi, **chưa phải sandbox bảo mật**.
- Parser dùng heuristic. Layout quá lạ (khác hẳn bảng thống kê hay bảng bản ghi) có thể bị đọc sai cấu trúc.
- Các câu cần ngầm hiểu quy ước riêng của người ra đề (không nêu trong câu hỏi) có thể bị trả lời theo cách hiểu khác.
