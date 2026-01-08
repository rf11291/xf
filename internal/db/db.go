package db

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

type Template struct {
	Subject string `json:"subject"`
	HTML    string `json:"html"`
}

var defaultRules = []int{30, 7, 1, 0}

var defaultTemplate = Template{
	Subject: "【续费提醒】{{ .Product.Name }} 将在 {{ .Product.ExpiresAt }} 到期",
	HTML: `<p>Hi {{ if .Customer.Name }}{{ .Customer.Name }}{{ else }}{{ .Customer.Email }}{{ end }},</p>
<p>你的产品 <b>{{ .Product.Name }}</b> 将在 <b>{{ .Product.ExpiresAt }}</b> 到期。</p>
<p>距离到期还剩 <b>{{ .DaysLeft }}</b> 天。</p>
{{ if .Product.Content }}<p>备注：{{ .Product.Content }}</p>{{ end }}
<hr/>
<p>如需继续续费使用，请登录续费管理面板或联系 support@example.com。</p>
<p>— {{ .Company }}</p>
`,
}

var defaultRenewalTemplate = Template{
	Subject: "【续费成功】{{ .Product.Name }} 已续费至 {{ .NewExpiresAt }}",
	HTML: `<p>Hi {{ if .Customer.Name }}{{ .Customer.Name }}{{ else }}{{ .Customer.Email }}{{ end }},</p>
<p>你的产品 <b>{{ .Product.Name }}</b> 已续费成功 ✅</p>
<p>原到期日：<b>{{ .OldExpiresAt }}</b></p>
<p>新到期日：<b>{{ .NewExpiresAt }}</b></p>
{{ if .Product.Content }}<p>产品信息：{{ .Product.Content }}</p>{{ end }}
<hr/>
<p>— {{ .Company }}</p>
`,
}

type Store struct {
	path string
	mu   sync.Mutex
	data snapshot
}

type snapshot struct {
	Customers     []Customer        `json:"customers"`
	Products      []Product         `json:"products"`
	Subscriptions []Subscription    `json:"subscriptions"`
	Settings      map[string]string `json:"settings"`
	DailySends    []DailySend       `json:"daily_sends"`
}

type DailySend struct {
	SubscriptionID int    `json:"subscription_id"`
	SentDate       string `json:"sent_date"`
	SentAt         string `json:"sent_at"`
}

type Customer struct {
	ID        int    `json:"id"`
	Email     string `json:"email"`
	Name      string `json:"name"`
	CreatedAt string `json:"created_at"`
}

type Product struct {
	ID        int    `json:"id"`
	Name      string `json:"name"`
	Content   string `json:"content"`
	CreatedAt string `json:"created_at"`
}

type Subscription struct {
	ID         int    `json:"id"`
	CustomerID int    `json:"customer_id"`
	ProductID  int    `json:"product_id"`
	ExpiresAt  string `json:"expires_at"`
	Note       string `json:"note"`
	CreatedAt  string `json:"created_at"`
}

type SubscriptionDetail struct {
	Subscription
	CustomerName   string
	CustomerEmail  string
	ProductName    string
	ProductContent string
}

func Open(path string) (*Store, error) {
	dir := filepath.Dir(path)
	if dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, err
		}
	}
	store := &Store{path: path}
	if err := store.load(); err != nil {
		return nil, err
	}
	if store.data.Settings == nil {
		store.data.Settings = map[string]string{}
	}
	if _, err := store.GetRules(); err != nil {
		return nil, err
	}
	if _, err := store.GetTemplate(); err != nil {
		return nil, err
	}
	if _, err := store.GetRenewalTemplate(); err != nil {
		return nil, err
	}
	return store, nil
}

func (s *Store) Close() error {
	return nil
}

func (s *Store) load() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	data, err := os.ReadFile(s.path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			s.data = snapshot{Settings: map[string]string{}}
			return s.saveLocked()
		}
		return err
	}
	if len(data) == 0 {
		s.data = snapshot{Settings: map[string]string{}}
		return nil
	}
	return json.Unmarshal(data, &s.data)
}

func (s *Store) saveLocked() error {
	payload, err := json.MarshalIndent(s.data, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(s.path, payload, 0o644)
}

func (s *Store) GetRules() ([]int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if value, ok := s.data.Settings["reminder_rules"]; ok {
		var rules []int
		if err := json.Unmarshal([]byte(value), &rules); err == nil && len(rules) > 0 {
			return rules, nil
		}
	}
	payload, _ := json.Marshal(defaultRules)
	s.data.Settings["reminder_rules"] = string(payload)
	if err := s.saveLocked(); err != nil {
		return nil, err
	}
	return defaultRules, nil
}

func (s *Store) UpdateRules(rules []int) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	payload, err := json.Marshal(rules)
	if err != nil {
		return err
	}
	s.data.Settings["reminder_rules"] = string(payload)
	return s.saveLocked()
}

func (s *Store) GetTemplate() (Template, error) {
	return s.getTemplate("email_template", defaultTemplate)
}

func (s *Store) GetRenewalTemplate() (Template, error) {
	return s.getTemplate("renewal_confirm_template", defaultRenewalTemplate)
}

func (s *Store) UpdateTemplate(tpl Template) error {
	return s.setTemplate("email_template", tpl)
}

func (s *Store) UpdateRenewalTemplate(tpl Template) error {
	return s.setTemplate("renewal_confirm_template", tpl)
}

func (s *Store) getTemplate(key string, fallback Template) (Template, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if value, ok := s.data.Settings[key]; ok {
		var tpl Template
		if err := json.Unmarshal([]byte(value), &tpl); err == nil && tpl.Subject != "" {
			return tpl, nil
		}
	}
	payload, _ := json.Marshal(fallback)
	s.data.Settings[key] = string(payload)
	if err := s.saveLocked(); err != nil {
		return Template{}, err
	}
	return fallback, nil
}

func (s *Store) setTemplate(key string, tpl Template) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	payload, err := json.Marshal(tpl)
	if err != nil {
		return err
	}
	s.data.Settings[key] = string(payload)
	return s.saveLocked()
}

func (s *Store) ListCustomers() ([]Customer, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := append([]Customer(nil), s.data.Customers...)
	sort.Slice(out, func(i, j int) bool { return out[i].ID > out[j].ID })
	return out, nil
}

func (s *Store) CreateCustomer(email, name string, now time.Time) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, c := range s.data.Customers {
		if c.Email == email {
			return fmt.Errorf("邮箱已存在")
		}
	}
	nextID := s.nextCustomerID()
	s.data.Customers = append(s.data.Customers, Customer{
		ID:        nextID,
		Email:     email,
		Name:      name,
		CreatedAt: now.Format(time.RFC3339),
	})
	return s.saveLocked()
}

func (s *Store) GetCustomer(id int) (Customer, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, c := range s.data.Customers {
		if c.ID == id {
			return c, nil
		}
	}
	return Customer{}, fmt.Errorf("客户不存在")
}

func (s *Store) DeleteCustomer(id int) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	var customers []Customer
	for _, c := range s.data.Customers {
		if c.ID != id {
			customers = append(customers, c)
		}
	}
	s.data.Customers = customers
	var subs []Subscription
	for _, sub := range s.data.Subscriptions {
		if sub.CustomerID != id {
			subs = append(subs, sub)
		}
	}
	s.data.Subscriptions = subs
	return s.saveLocked()
}

func (s *Store) ListProducts() ([]Product, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := append([]Product(nil), s.data.Products...)
	sort.Slice(out, func(i, j int) bool { return out[i].ID > out[j].ID })
	return out, nil
}

func (s *Store) CreateProduct(name, content string, now time.Time) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, p := range s.data.Products {
		if p.Name == name {
			return fmt.Errorf("产品名称已存在")
		}
	}
	nextID := s.nextProductID()
	s.data.Products = append(s.data.Products, Product{
		ID:        nextID,
		Name:      name,
		Content:   content,
		CreatedAt: now.Format(time.RFC3339),
	})
	return s.saveLocked()
}

func (s *Store) GetProduct(id int) (Product, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, p := range s.data.Products {
		if p.ID == id {
			return p, nil
		}
	}
	return Product{}, fmt.Errorf("产品不存在")
}

func (s *Store) DeleteProduct(id int) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, sub := range s.data.Subscriptions {
		if sub.ProductID == id {
			return fmt.Errorf("产品已被订阅，无法删除")
		}
	}
	var products []Product
	for _, p := range s.data.Products {
		if p.ID != id {
			products = append(products, p)
		}
	}
	s.data.Products = products
	return s.saveLocked()
}

func (s *Store) ListSubscriptions() ([]SubscriptionDetail, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []SubscriptionDetail
	for _, sub := range s.data.Subscriptions {
		customer, _ := s.findCustomer(sub.CustomerID)
		product, _ := s.findProduct(sub.ProductID)
		out = append(out, SubscriptionDetail{
			Subscription:   sub,
			CustomerName:   customer.Name,
			CustomerEmail:  customer.Email,
			ProductName:    product.Name,
			ProductContent: product.Content,
		})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID > out[j].ID })
	return out, nil
}

func (s *Store) CreateSubscription(customerID, productID int, expiresAt, note string, now time.Time) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.findCustomer(customerID); !ok {
		return fmt.Errorf("客户不存在")
	}
	if _, ok := s.findProduct(productID); !ok {
		return fmt.Errorf("产品不存在")
	}
	nextID := s.nextSubscriptionID()
	s.data.Subscriptions = append(s.data.Subscriptions, Subscription{
		ID:         nextID,
		CustomerID: customerID,
		ProductID:  productID,
		ExpiresAt:  expiresAt,
		Note:       note,
		CreatedAt:  now.Format(time.RFC3339),
	})
	return s.saveLocked()
}

func (s *Store) GetSubscription(id int) (SubscriptionDetail, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, sub := range s.data.Subscriptions {
		if sub.ID == id {
			customer, _ := s.findCustomer(sub.CustomerID)
			product, _ := s.findProduct(sub.ProductID)
			return SubscriptionDetail{
				Subscription:   sub,
				CustomerName:   customer.Name,
				CustomerEmail:  customer.Email,
				ProductName:    product.Name,
				ProductContent: product.Content,
			}, nil
		}
	}
	return SubscriptionDetail{}, fmt.Errorf("订阅不存在")
}

func (s *Store) UpdateSubscription(id int, expiresAt, note string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	for i, sub := range s.data.Subscriptions {
		if sub.ID == id {
			s.data.Subscriptions[i].ExpiresAt = expiresAt
			s.data.Subscriptions[i].Note = note
			return s.saveLocked()
		}
	}
	return fmt.Errorf("订阅不存在")
}

func (s *Store) DeleteSubscription(id int) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	var subs []Subscription
	for _, sub := range s.data.Subscriptions {
		if sub.ID != id {
			subs = append(subs, sub)
		}
	}
	s.data.Subscriptions = subs
	return s.saveLocked()
}

func (s *Store) CountStats() (customers, products, subs int, err error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.data.Customers), len(s.data.Products), len(s.data.Subscriptions), nil
}

func (s *Store) ListDueSubscriptions() ([]SubscriptionDetail, error) {
	return s.ListSubscriptions()
}

func (s *Store) HasDailySend(subscriptionID int, date string) (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, send := range s.data.DailySends {
		if send.SubscriptionID == subscriptionID && send.SentDate == date {
			return true, nil
		}
	}
	return false, nil
}

func (s *Store) RecordDailySend(subscriptionID int, date string, now time.Time) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.data.DailySends = append(s.data.DailySends, DailySend{
		SubscriptionID: subscriptionID,
		SentDate:       date,
		SentAt:         now.Format(time.RFC3339),
	})
	return s.saveLocked()
}

func (s *Store) nextCustomerID() int {
	max := 0
	for _, c := range s.data.Customers {
		if c.ID > max {
			max = c.ID
		}
	}
	return max + 1
}

func (s *Store) nextProductID() int {
	max := 0
	for _, c := range s.data.Products {
		if c.ID > max {
			max = c.ID
		}
	}
	return max + 1
}

func (s *Store) nextSubscriptionID() int {
	max := 0
	for _, c := range s.data.Subscriptions {
		if c.ID > max {
			max = c.ID
		}
	}
	return max + 1
}

func (s *Store) findCustomer(id int) (Customer, bool) {
	for _, c := range s.data.Customers {
		if c.ID == id {
			return c, true
		}
	}
	return Customer{}, false
}

func (s *Store) findProduct(id int) (Product, bool) {
	for _, p := range s.data.Products {
		if p.ID == id {
			return p, true
		}
	}
	return Product{}, false
}
