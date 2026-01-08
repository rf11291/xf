package email

import (
	"bytes"
	"net/smtp"
	"strings"
	"time"
)

type Mailer struct {
	Host string
	Port int
	User string
	Pass string
	From string
}

func (m Mailer) Enabled() bool {
	return m.Host != "" && m.From != ""
}

func (m Mailer) Send(to, subject, htmlBody string) error {
	if !m.Enabled() {
		return fmt.Errorf("SMTP is not configured")
	}

	addr := fmt.Sprintf("%s:%d", m.Host, m.Port)
	auth := smtp.PlainAuth("", m.User, m.Pass, m.Host)
	boundary := fmt.Sprintf("xf-%d", time.Now().UnixNano())

	var msg bytes.Buffer
	msg.WriteString(fmt.Sprintf("From: %s\r\n", m.From))
	msg.WriteString(fmt.Sprintf("To: %s\r\n", to))
	msg.WriteString(fmt.Sprintf("Subject: %s\r\n", encodeHeader(subject)))
	msg.WriteString("MIME-Version: 1.0\r\n")
	msg.WriteString(fmt.Sprintf("Content-Type: multipart/alternative; boundary=%q\r\n", boundary))
	msg.WriteString("\r\n")
	msg.WriteString(fmt.Sprintf("--%s\r\n", boundary))
	msg.WriteString("Content-Type: text/plain; charset=utf-8\r\n\r\n")
	msg.WriteString(stripHTML(htmlBody))
	msg.WriteString("\r\n")
	msg.WriteString(fmt.Sprintf("--%s\r\n", boundary))
	msg.WriteString("Content-Type: text/html; charset=utf-8\r\n\r\n")
	msg.WriteString(htmlBody)
	msg.WriteString("\r\n")
	msg.WriteString(fmt.Sprintf("--%s--\r\n", boundary))

}

func extractAddress(input string) string {
	if idx := strings.LastIndex(input, "<"); idx != -1 {
		if end := strings.LastIndex(input, ">"); end != -1 && end > idx {
			return strings.TrimSpace(input[idx+1 : end])
		}
	}
	return strings.TrimSpace(input)
}

func encodeHeader(value string) string {
	return value
}

func stripHTML(input string) string {
	out := strings.ReplaceAll(input, "<br>", "\n")
	out = strings.ReplaceAll(out, "<br/>", "\n")
	out = strings.ReplaceAll(out, "<br />", "\n")
	return stripTags(out)
}

func stripTags(input string) string {
	var b strings.Builder
	inTag := false
	for _, r := range input {
		switch r {
		case '<':
			inTag = true
		case '>':
			inTag = false
		default:
			if !inTag {
				b.WriteRune(r)
			}
		}
	}
	return b.String()
}
