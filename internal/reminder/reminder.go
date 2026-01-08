package reminder

import (
	"fmt"
	"sort"
	"strings"
	"time"

	"xf/internal/db"
	"xf/internal/email"
)

type Renderer interface {
	RenderTemplate(tpl db.Template, data any) (subject, html string, err error)
}

type Service struct {
	Store    *db.Store
	Mailer   email.Mailer
	Company  string
	Location *time.Location
	Render   Renderer
}

type Result struct {
	Total    int
	Sent     int
	Skipped  int
	Failed   int
	Failures []string
}

func (s Service) ScanAndSend(now time.Time) (Result, error) {
	subs, err := s.Store.ListDueSubscriptions()
	if err != nil {
		return Result{}, err
	}
	rules, err := s.Store.GetRules()
	if err != nil {
		return Result{}, err
	}
	maxRule := maxInt(rules)

	var res Result
	for _, sub := range subs {
		res.Total++
		daysLeft, err := daysUntil(sub.ExpiresAt, now, s.Location)
		if err != nil {
			res.Failed++
			res.Failures = append(res.Failures, fmt.Sprintf("订阅 #%d 日期格式错误", sub.ID))
			continue
		}
		if daysLeft < -1 {
			res.Skipped++
			continue
		}
		if daysLeft > maxRule {
			res.Skipped++
			continue
		}
		sentDate := now.In(s.Location).Format("2006-01-02")
		exists, err := s.Store.HasDailySend(sub.ID, sentDate)
		if err != nil {
			res.Failed++
			res.Failures = append(res.Failures, fmt.Sprintf("订阅 #%d 检查发送记录失败", sub.ID))
			continue
		}
		if exists {
			res.Skipped++
			continue
		}
		if err := s.sendReminder(sub, daysLeft); err != nil {
			res.Failed++
			res.Failures = append(res.Failures, fmt.Sprintf("订阅 #%d 发送失败: %s", sub.ID, err))
			continue
		}
		if err := s.Store.RecordDailySend(sub.ID, sentDate, now); err != nil {
			res.Failures = append(res.Failures, fmt.Sprintf("订阅 #%d 记录发送失败", sub.ID))
		}
		res.Sent++
	}
	return res, nil
}

func (s Service) SendNow(threshold int, now time.Time) (Result, error) {
	subs, err := s.Store.ListDueSubscriptions()
	if err != nil {
		return Result{}, err
	}
	var res Result
	for _, sub := range subs {
		res.Total++
		daysLeft, err := daysUntil(sub.ExpiresAt, now, s.Location)
		if err != nil || daysLeft < -1 {
			res.Skipped++
			continue
		}
		if daysLeft > threshold {
			res.Skipped++
			continue
		}
		if err := s.sendReminder(sub, daysLeft); err != nil {
			res.Failed++
			res.Failures = append(res.Failures, fmt.Sprintf("订阅 #%d 发送失败: %s", sub.ID, err))
			continue
		}
		res.Sent++
	}
	return res, nil
}

func (s Service) SendRenewalConfirm(sub db.SubscriptionDetail, oldExpires, newExpires string) error {
	tpl, err := s.Store.GetRenewalTemplate()
	if err != nil {
		return err
	}
	data := buildTemplateData(sub, s.Company, 0)
	data["OldExpiresAt"] = oldExpires
	data["NewExpiresAt"] = newExpires
	subject, html, err := s.Render.RenderTemplate(tpl, data)
	if err != nil {
		return err
	}
	return s.Mailer.Send(sub.CustomerEmail, subject, html)
}

func (s Service) sendReminder(sub db.SubscriptionDetail, daysLeft int) error {
	tpl, err := s.Store.GetTemplate()
	if err != nil {
		return err
	}
	data := buildTemplateData(sub, s.Company, daysLeft)
	subject, html, err := s.Render.RenderTemplate(tpl, data)
	if err != nil {
		return err
	}
	return s.Mailer.Send(sub.CustomerEmail, subject, html)
}

func buildTemplateData(sub db.SubscriptionDetail, company string, daysLeft int) map[string]any {
	content := strings.TrimSpace(sub.Note)
	if content == "" {
		content = sub.ProductContent
	}
	product := map[string]any{
		"ID":        sub.ProductID,
		"Name":      sub.ProductName,
		"Content":   content,
		"ExpiresAt": sub.ExpiresAt,
	}
	customer := map[string]any{
		"ID":    sub.CustomerID,
		"Name":  sub.CustomerName,
		"Email": sub.CustomerEmail,
	}
	subscription := map[string]any{
		"ID":         sub.ID,
		"CustomerID": sub.CustomerID,
		"ProductID":  sub.ProductID,
		"ExpiresAt":  sub.ExpiresAt,
		"Note":       sub.Note,
	}
	return map[string]any{
		"Customer":     customer,
		"ProductDef":   product,
		"Subscription": subscription,
		"Product":      product,
		"DaysLeft":     daysLeft,
		"DaysBefore":   daysLeft,
		"Now":          time.Now().Format(time.RFC3339),
		"Company":      company,
	}
}

func daysUntil(date string, now time.Time, loc *time.Location) (int, error) {
	t, err := time.ParseInLocation("2006-01-02", date, loc)
	if err != nil {
		return 0, err
	}
	start := now.In(loc).Truncate(24 * time.Hour)
	target := t.Truncate(24 * time.Hour)
	return int(target.Sub(start).Hours() / 24), nil
}

func ParseRules(input string) ([]int, error) {
	input = strings.TrimSpace(input)
	if input == "" {
		return nil, fmt.Errorf("规则不能为空")
	}
	parts := strings.Split(input, ",")
	var rules []int
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		var value int
		if _, err := fmt.Sscanf(p, "%d", &value); err != nil {
			return nil, fmt.Errorf("无效规则: %s", p)
		}
		rules = append(rules, value)
	}
	if len(rules) == 0 {
		return nil, fmt.Errorf("规则不能为空")
	}
	sort.Ints(rules)
	return rules, nil
}

func maxInt(values []int) int {
	max := values[0]
	for _, v := range values {
		if v > max {
			max = v
		}
	}
	return max
}
