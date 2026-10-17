# Thiết kế prompt

Tài liệu này mô tả cách các prompt của hệ thống được cấu trúc và lý do thiết kế. Toàn bộ code nằm trong `excelqa/solver.py` (các prompt, khối quy ước) và `excelqa/examples.py` (ví dụ đã giải).

## 1. Mục tiêu thiết kế
- **Đúng quy ước của người chấm.** Đáp án chuẩn tuân theo những quy ước mà câu hỏi không phải lúc nào cũng nói ra, ví dụ có tính dòng tổng con hay không, tuổi tính tại ngày nào, xếp hạng ở cấp nào. Prompt phải nêu các quy ước đó thành **nguyên tắc chung**, áp dụng được cho bảng và câu hỏi chưa từng gặp, chứ không vá theo từng trường hợp.
- **Nhất quán giữa các ứng viên.** Hệ thống bỏ phiếu giữa nhiều ứng viên (chương trình pandas, đọc trực tiếp). Nếu mỗi ứng viên được dặn một kiểu, chúng sẽ bất đồng vì chỉ dẫn chứ không vì dữ liệu.
- **Ngắn và dễ tuân thủ với một model nhỏ.** Mỗi chỉ dẫn thêm vào đều làm giảm khả năng tuân thủ các chỉ dẫn còn lại.

## 2. Cấu trúc các prompt
Có ba prompt hệ thống, cùng dựng theo khung *đầu – quy ước – đuôi*:

| Prompt | Phần đầu (vai trò, giao diện dữ liệu) | Phần đuôi (đầu ra) |
|---|---|---|
| `PROGRAM`: viết chương trình pandas | Mô tả các DataFrame `T0, T1, …` (`row`, `path`, `level_k`, `is_summary`, `has_children`), cột dẫn xuất, bảng `facts`, các hàm hỗ trợ; quy tắc "đọc mọi con số từ DataFrame, không tự tính nhẩm" | Kế hoạch JSON (`interpretation`, `assumptions`, `cells`, `output`) rồi mới đến khối code, kết quả gán vào biến `answer` |
| `DIRECT`: đọc trực tiếp bản đồ bảng | Cách đọc bản đồ, khối FOCUS, ý nghĩa các cột dẫn xuất | Một dòng `Reading:` trước khi tính, kết thúc bằng `ANSWER:` |
| `ARBITER`: trọng tài khi bỏ phiếu hòa | Đối chiếu từng ứng viên với câu hỏi theo nghĩa đen. Trọng tài chỉ **chọn** trong số các ứng viên đã có, không tự sinh đáp án mới | JSON `{"choice", "reason"}` |

Lượt user được sắp xếp theo thứ tự: **dữ liệu trước, câu hỏi sau cùng**.

```
[FOCUS: các ô có header khớp với câu hỏi]   ← nếu có
MAP: bản đồ toàn bộ workbook
[SOLVED EXAMPLES]                           ← nếu bật
[ANSWER-KEY CONVENTIONS]                    ← nếu đặt quy ước ở cuối (QA_CONV_POS=end)
Question: …
```

## 3. Khối quy ước dùng chung (`CONVENTIONS`)

### Nguyên tắc viết
1. **Một khối duy nhất, giống hệt nhau từng chữ ở cả ba prompt.** Đây là nguồn quy ước duy nhất của hệ thống. Ba danh sách luật viết riêng sẽ lệch nhau theo thời gian, và model nhỏ giải quyết mâu thuẫn chỉ dẫn rất kém.
2. **Thứ tự ưu tiên tường minh**, ghi ngay dòng đầu: *lời lẽ tường minh của câu hỏi luôn thắng; quy ước đứng trước thắng quy ước đứng sau*. Nhờ vậy hai quy ước có vẻ va nhau (ví dụ "không gộp cha với con" và "*all* thì tính cả tổng con") có cách giải rõ ràng.
3. **Ít quy ước, mỗi quy ước một ý, dạng "khi … thì …"**, quy ước quan trọng nhất đặt trước.
4. **Giữ động từ cốt lõi khi rút gọn.** Rút gọn câu chữ không được làm mất hành động chính của quy ước. Ví dụ, quy ước về đơn vị phải nói rõ "không nhân, không chia theo đơn vị", chứ không chỉ "giữ đơn vị".
5. **Không chứa quy tắc định dạng.** Số chữ số thập phân, dấu phân cách, chữ hoa thường do code tất định xử lý (mục 5). Prompt chỉ giữ các quy ước về **nghĩa**.
6. **Không dùng ví dụ lấy từ dữ liệu cụ thể** trong câu chữ của quy ước, để model không bám vào chi tiết bề mặt.

### Chín quy ước và lý do thiết kế

| # | Quy ước | Lý do thiết kế |
|---|---|---|
| 1 | Câu hỏi nêu cấp hoặc bộ lọc ("directly under X", "detailed", "excluding totals", "only …") thì dùng đúng cấp đó | Đặt đầu tiên để mọi quy ước mặc định phía sau chỉ áp dụng khi câu hỏi không nói gì |
| 2 | "All / across all / every X" khi không nêu cấp: tính mọi mục X trong phạm vi, kể cả mục tổng con; trung bình là trung bình cộng không trọng số | Quy ước chính thức của người chấm. Đặt trước quy ước 3 để từ "all" thắng quy ước mặc định về cấp |
| 3 | Không nêu cấp và không nói "all": so hoặc xếp hạng các mục cấp trên cùng (ngay dưới tổng chung); không gộp một mục với mục con của nó; không tính tổng chung | Cách đọc mặc định khi câu hỏi im lặng về cấp. Tránh hai lỗi phổ biến: so lẫn cha với con, và tự giới hạn vào dòng lá |
| 4 | Một dòng và một cột thì lấy ô giao nhau; chỉ cộng khi câu hỏi nói sum/total/combined/both | Chặn việc tự cộng nhiều ô khi câu hỏi chỉ định một ô |
| 5 | Bản ghi có ngày sự kiện riêng (nhập học, tuyển dụng…) thì tuổi hoặc thời gian trôi qua tính tại ngày đó, kể cả khi câu hỏi không nhắc lại; xếp theo số ngày chính xác | Nêu ở dạng tổng quát (mọi thuộc tính phụ thuộc thời gian), không riêng cho tuổi. Xếp theo ngày vì tuổi tròn năm dễ bằng nhau |
| 6 | "Từ A đến B" là B − A; % thay đổi là (B − A)/A × 100; điểm phần trăm là hiệu hai tỷ lệ; "tỷ số A trên B" là A/B | Định nghĩa toán học chuẩn. Nêu ra vì model hay nhầm chiều của phép tính |
| 7 | Tỷ lệ, tỷ trọng, trung bình, chỉ số không cộng thành tổng, trừ khi câu hỏi yêu cầu | Kèm sẵn ngoại lệ, để quy ước không chặn các câu hỏi cố ý yêu cầu cộng |
| 8 | Số đã ở đơn vị của bảng: trả lời đúng số như bảng ghi, không nhân hay chia theo đơn vị, trừ khi câu hỏi đổi đơn vị | Bảng ghi "in thousands" dễ khiến model tự nhân thêm. Câu chữ giữ nguyên động từ "never multiply or divide" |
| 9 | Tên lấy theo nhãn trong bảng; khi câu hỏi liệt kê các lựa chọn thì dùng cách viết của câu hỏi | Phân biệt rõ hai trường hợp, để nhãn chỉ dùng để định vị dữ liệu không bị coi là đáp án |

## 4. Nêu cách hiểu trước khi tính
- Kế hoạch JSON của ứng viên chương trình có trường **`assumptions`**: các quy ước (theo số thứ tự) quyết định chỗ mơ hồ, hoặc `none`. Trường **`cells`**: các dòng và cột sẽ dùng. Cả hai phải viết **trước** khi viết code.
- Ứng viên đọc trực tiếp viết một dòng **`Reading:`** (dòng, cột và quy ước áp dụng) trước khi tính.
- Lý do: khi không được nhắc, model chọn cách hiểu theo thói quen. Bắt nêu cách hiểu trước khiến quy ước được áp dụng có chủ đích, và đưa cách hiểu ra trace để trọng tài và người phân tích xem được.

## 5. Phân công giữa prompt và code
Những gì code làm được một cách tất định thì không giao cho prompt.

| Việc | Do code xử lý |
|---|---|
| Định dạng đáp án (số chữ số thập phân theo câu hỏi bằng 5 ngôn ngữ, làm tròn half-up, dấu phân cách danh sách) | `render_answer`, `formatting.py` |
| Lọc ứng viên theo kiểu đáp án (Yes/No, số, danh sách) và theo mẫu đáp án do câu hỏi cho sẵn | `ground.expected_type`, `ground.answer_template` |
| Bỏ phiếu trên dạng chuẩn hóa; xuất bằng đúng cách viết trong bảng | `solve` |
| Tuổi và thời gian trôi qua tại ngày sự kiện; tổng của nhóm cột con | Cột dẫn xuất trong DataFrame |
| Cột phần trăm được lưu dạng phân số (1 = 100%) | Ghi chú trong bản đồ bảng |
| Sai số máy của số thực (`int()` không cắt 90.9999… thành 90) | Hàm `int` an toàn trong môi trường chạy chương trình |
| Văn bản trong ô là dữ liệu, không phải chỉ dẫn | Đánh dấu văn bản đáng ngờ trong bản đồ |

## 6. Ví dụ đã giải (`excelqa/examples.py`, mặc định tắt)
- Chèn tối đa *k* câu hỏi đã giải, mỗi câu kèm cách đọc theo quy ước. Ví dụ luôn lấy từ **workbook khác** với câu hỏi đang trả lời, nên nó dạy **cách đọc** chứ không dạy giá trị.
- Độ giống giữa hai câu được tính trên **khung câu hỏi**:
  - các từ có trong nhãn của chính workbook đó (từ thuộc miền dữ liệu) bị che;
  - câu chỉ dẫn định dạng ở cuối câu hỏi bị bỏ.

  Nhờ vậy ví dụ được ghép theo **dạng suy luận** ("second-oldest … on the hire date", "directly under", "percentage points"), không theo chủ đề.
- Chỉ chèn khi độ giống vượt ngưỡng, và mỗi dạng câu hỏi chỉ lấy một ví dụ. Không có ví dụ đủ giống thì không chèn gì, vì ví dụ không liên quan có thể gây hại.
- Kho ví dụ: `excelqa/examples_bank.json`.

## 7. Cấu hình

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `QA_CONV_POS` | `system` | `system`: khối quy ước nằm trong system prompt. `end`: nằm sau dữ liệu, ngay trước câu hỏi |
| `QA_EXAMPLES` | `0` | Số ví dụ đã giải chèn vào mỗi câu (0 = tắt) |

## 8. Cơ sở lý thuyết

| Quyết định | Tài liệu |
|---|---|
| Ghi quy ước miền thành luật tường minh do người viết | Li et al., *BIRD*, NeurIPS 2023 (external knowledge evidence) |
| Ít chỉ dẫn, mỗi chỉ dẫn một ý; chỉ dẫn quan trọng đặt trước | Jiang et al., *FollowBench*, ACL 2024; Jaroslawicz et al., *IFScale*, 2025 |
| Một khối luật dùng chung, có thứ tự ưu tiên, không mâu thuẫn | Zhang et al., *IHEval*, NAACL 2025; Wallace et al., *The Instruction Hierarchy*, 2024 |
| Dữ liệu trước, chỉ dẫn và câu hỏi sau | Liu et al., *Lost in the Middle*, TACL 2024 |
| Nêu cách hiểu trước khi tính | Saparina & Lapata, *Disambiguate First, Parse Later*, ACL 2025 Findings; *AMBROSIA*, NeurIPS 2024; Wang et al., *Plan-and-Solve*, ACL 2023 |
| Ví dụ giống câu hỏi (che từ miền), không ngẫu nhiên | Gao et al., *DAIL-SQL*, VLDB 2024 |
| Hai cách suy luận (code và văn bản) rồi bỏ phiếu; trọng tài chỉ chọn trong số ứng viên | Liu et al., *Rethinking Tabular Data Understanding with LLMs* (Mix self-consistency), NAACL 2024 |
| Không dùng tự sửa khi không có phản hồi từ bên ngoài | Huang et al., *Large Language Models Cannot Self-Correct Reasoning Yet*, ICLR 2024 |

## 9. Quy tắc khi sửa prompt
- **Thêm quy ước** chỉ khi đó là quy ước của người chấm có căn cứ (tài liệu chính thức, hoặc lặp lại trên nhiều bảng khác nhau), viết ở dạng tổng quát và đặt đúng thứ tự ưu tiên. Không thêm luật để chữa một câu hỏi cụ thể.
- **Sửa câu chữ** thì giữ nguyên động từ cốt lõi của quy ước. Kiểm tra lại các câu hỏi liên quan tới quy ước đó.
- **Mọi thay đổi** phải giữ khối quy ước giống hệt nhau ở cả ba prompt, và được đo trước khi đưa vào bản chính: không giảm trên tập kiểm chứng, và tăng có ý nghĩa ở nơi thay đổi nhắm tới.
- **Tín hiệu mới trong bản đồ bảng** (cờ, nhãn, chú thích) chỉ thêm khi đo được là có lợi. Một tín hiệu nghe hợp lý vẫn có thể kéo model sang cách hiểu sai.
