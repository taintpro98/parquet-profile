package main

import (
	"fmt"
	"math/rand/v2"
	"strings"
	"unicode/utf8"
)

var (
	words = strings.Fields(`chuyển khoản thanh toán hóa đơn tiền điện nước học phí lương tháng hoàn trả
		phí dịch vụ nạp tiền điện thoại mua hàng trực tuyến tiết kiệm gửi góp vay trả nợ thẻ tín dụng
		ngân hàng chi nhánh giao dịch viên khách hàng tài khoản nguồn đích ghi chú nội dung theo hợp đồng
		số đơn hàng mã giao dịch xác nhận thành công đang xử lý bị từ chối hoàn tất qua ứng dụng internet
		banking ATM POS QR chuyển nhanh liên ngân hàng napas citad swift ngoại tệ tỷ giá USD EUR VND
		phí quản lý tài khoản bảo hiểm khoản vay lãi suất kỳ hạn tất toán rút trước hạn tiền mặt quầy`)
	familyNames = strings.Fields("Nguyễn Trần Lê Phạm Hoàng Huỳnh Phan Vũ Võ Đặng Bùi Đỗ Hồ Ngô Dương Lý")
	middleNames = strings.Fields("Văn Thị Hữu Đức Minh Thanh Ngọc Quang Thu Bảo Gia Anh Xuân Hoài")
	givenNames  = strings.Fields("An Bình Chi Dũng Giang Hà Hải Hạnh Hùng Hương Khánh Lan Linh Long Mai Nam Phong Phương Quân Sơn Tâm Thảo Trang Tuấn Vy")
	streets     = strings.Fields("Láng_Hạ Trần_Duy_Hưng Nguyễn_Trãi Lê_Lợi Hai_Bà_Trưng Điện_Biên_Phủ Cách_Mạng_Tháng_Tám Võ_Văn_Kiệt")
	districts   = strings.Fields("Đống_Đa Cầu_Giấy Thanh_Xuân Ba_Đình Hoàn_Kiếm Quận_1 Quận_3 Bình_Thạnh Hải_Châu Ninh_Kiều")
	cities      = strings.Fields("Hà_Nội TP_Hồ_Chí_Minh Đà_Nẵng Cần_Thơ Hải_Phòng")
	statuses    = strings.Fields("SUCCESS PENDING FAILED REVERSED TIMEOUT CANCELLED")
	channels    = strings.Fields("APP WEB ATM POS COUNTER API")
)

const alnum = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

func pick(rnd *rand.Rand, list []string) string {
	return strings.ReplaceAll(list[rnd.IntN(len(list))], "_", " ")
}

func digits(rnd *rand.Rand, n int) string {
	b := make([]byte, n)
	for i := range b {
		b[i] = byte('0' + rnd.IntN(10))
	}
	return string(b)
}

// sentence ghép từ ngẫu nhiên tới khoảng độ dài mong muốn, không vượt 1000 byte của VARCHAR2.
func sentence(rnd *rand.Rand, minLen, maxLen int) string {
	target := minLen + rnd.IntN(maxLen-minLen+1)
	var b strings.Builder
	for b.Len() < target {
		if b.Len() > 0 {
			b.WriteByte(' ')
		}
		if rnd.IntN(8) == 0 {
			b.WriteString(digits(rnd, 3+rnd.IntN(8)))
		} else {
			b.WriteString(pick(rnd, words))
		}
	}
	s := b.String()
	for len(s) > 1000 {
		s = s[:strings.LastIndexByte(s, ' ')]
	}
	return s
}

// heads: phần đầu mang "kiểu" của cột (họ tên, số tài khoản, số tiền, mã, địa chỉ…). Phần còn lại
// của giá trị là câu ghép từ ngẫu nhiên cho đủ độ dài.
var heads = []func(*rand.Rand) string{
	func(r *rand.Rand) string { return "" }, // mô tả thuần
	func(r *rand.Rand) string { // họ tên
		name := pick(r, familyNames) + " " + pick(r, middleNames) + " " + pick(r, givenNames)
		if r.IntN(2) == 0 {
			return strings.ToUpper(name)
		}
		return name
	},
	func(r *rand.Rand) string { return "TK " + digits(r, 10+r.IntN(7)) }, // số tài khoản
	func(r *rand.Rand) string { // số tiền
		return fmt.Sprintf("%d.%02d VND", r.Int64N(5_000_000_000), r.IntN(100))
	},
	func(r *rand.Rand) string { // mã
		b := make([]byte, 6+r.IntN(7))
		for i := range b {
			b[i] = alnum[r.IntN(len(alnum))]
		}
		return string(b)
	},
	func(r *rand.Rand) string { // địa chỉ
		return fmt.Sprintf("Số %d %s, %s, %s", 1+r.IntN(500), pick(r, streets), pick(r, districts), pick(r, cities))
	},
	func(r *rand.Rand) string { return pick(r, statuses) }, // trạng thái
	func(r *rand.Rand) string { // metadata dạng JSON
		return fmt.Sprintf(`{"channel":"%s","device":"%s","ip":"10.%d.%d.%d","session":"%s"}`,
			pick(r, channels), digits(r, 12), r.IntN(256), r.IntN(256), r.IntN(256), digits(r, 20))
	},
}

// textValue sinh giá trị cột TEXT_nn (column tính từ 1), hoặc ok=false cho NULL.
//
// Kích thước giống bộ dữ liệu cũ (khoảng 21 KB mỗi dòng): mỗi cột 100–499 byte; TEXT_02 NULL
// khoảng 1/3 số dòng; TEXT_03 là ghi chú ngắn khoảng 30 byte.
func textValue(r *rand.Rand, column int) (value string, ok bool) {
	switch column {
	case 2:
		if r.IntN(3) == 0 {
			return "", false
		}
	case 3:
		return sentence(r, 20, 40), true
	}
	target := 100 + r.IntN(400)
	head := heads[(column-1)%len(heads)](r)
	if len(head) >= target {
		return head, true
	}
	if head != "" {
		head += " "
	}
	return fill(head+sentence(r, target-len(head), target-len(head)), target), true
}

// fill cắt về đúng target byte mà không làm hỏng ký tự UTF-8.
func fill(s string, target int) string {
	if len(s) <= target {
		return s
	}
	for target > 0 && !utf8.RuneStart(s[target]) {
		target--
	}
	return s[:target]
}
