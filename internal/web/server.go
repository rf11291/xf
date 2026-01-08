package web

import (
	"embed"
	"fmt"
	"html/template"
	"io"
codex-ycpf8l
	"io/fs"
=======
main
	"net/http"
	"path"
	"strconv"
	"strings"
	"time"

	"xf/internal/config"
	"xf/internal/db"
	"xf/internal/email"
	"xf/internal/reminder"
)

//go:embed templates/*.html assets/*
var assetsFS embed.FS

type Server struct {
	cfg      config.Config
	store    *db.Store
	mailer   email.Mailer
	reminder reminder.Service
}

type PageData struct {
	Title           string
	Company         string
	Flash           string
	Stats           struct{ Customers, Products, Subscriptions int }
	Rules           []int
	RulesInput      string
	ScanThreshold   int
	Customers       []db.Customer
	Products        []db.Product
	Subscriptions   []db.SubscriptionDetail
	Customer        db.Customer
	Product         db.Product
	Subscription    db.SubscriptionDetail
	Template        db.Template
	RenewalTemplate db.Template
}

type TemplateRenderer struct{}

func (TemplateRenderer) RenderTemplate(tpl db.Template, data any) (string, string, error) {
	subject, err := renderText(tpl.Subject, data)
	if err != nil {
		return "", "", err
	}
	htmlBody, err := renderHTML(tpl.HTML, data)
	if err != nil {
		return "", "", err
	}
	return subject, htmlBody, nil
}

func NewServer(cfg config.Config, store *db.Store, mailer email.Mailer) (*Server, error) {
	renderer := TemplateRenderer{}
	reminderService := reminder.Service{
		Store:    store,
		Mailer:   mailer,
		Company:  cfg.CompanyName,
		Location: cfg.TimeZone,
		Render:   renderer,
	}
	return &Server{
		cfg:      cfg,
		store:    store,
		mailer:   mailer,
		reminder: reminderService,
	}, nil
}

func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()
codex-ycpf8l
	assetsSub, err := fs.Sub(assetsFS, "assets")
	if err != nil {
		panic(err)
	}
	mux.Handle("/assets/", http.StripPrefix("/assets/", http.FileServer(http.FS(assetsSub))))
=======
	mux.Handle("/assets/", http.StripPrefix("/assets/", http.FileServer(http.FS(assetsFS))))
main
	mux.HandleFunc("/", s.auth(s.handleDashboard))
	mux.HandleFunc("/customers", s.auth(s.handleCustomers))
	mux.HandleFunc("/customers/", s.auth(s.handleCustomerDetail))
	mux.HandleFunc("/products", s.auth(s.handleProducts))
	mux.HandleFunc("/products/", s.auth(s.handleProductDetail))
	mux.HandleFunc("/subscriptions", s.auth(s.handleSubscriptions))
	mux.HandleFunc("/subscriptions/", s.auth(s.handleSubscriptionDetail))
	mux.HandleFunc("/settings", s.auth(s.handleSettings))
	mux.HandleFunc("/settings/", s.auth(s.handleSettingsActions))
	mux.HandleFunc("/scan", s.auth(s.handleScan))
	return mux
}

func (s *Server) auth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		user, pass, ok := r.BasicAuth()
		if !ok || user != s.cfg.AdminUser || pass != s.cfg.AdminPass {
			w.Header().Set("WWW-Authenticate", `Basic realm="Renewal Panel"`)
			http.Error(w, "Unauthorized", http.StatusUnauthorized)
			return
		}
		next(w, r)
	}
}

func (s *Server) handleDashboard(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	customers, products, subs, err := s.store.CountStats()
	if err != nil {
		s.renderError(w, err)
		return
	}
	rules, _ := s.store.GetRules()
	data := PageData{
		Title:         "概览",
		Company:       s.cfg.CompanyName,
		Rules:         rules,
		ScanThreshold: maxInt(rules),
	}
	data.Stats.Customers = customers
	data.Stats.Products = products
	data.Stats.Subscriptions = subs
	s.render(w, "dashboard.html", data)
}

func (s *Server) handleCustomers(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		customers, err := s.store.ListCustomers()
		if err != nil {
			s.renderError(w, err)
			return
		}
		data := PageData{
			Title:     "客户管理",
			Company:   s.cfg.CompanyName,
			Customers: customers,
		}
		s.render(w, "customers.html", data)
	case http.MethodPost:
		if err := r.ParseForm(); err != nil {
			s.renderError(w, err)
			return
		}
		email := strings.TrimSpace(r.FormValue("email"))
		name := strings.TrimSpace(r.FormValue("name"))
		if email == "" {
			s.renderMessage(w, "邮箱不能为空", "/customers")
			return
		}
		if err := s.store.CreateCustomer(email, name, time.Now()); err != nil {
			s.renderMessage(w, fmt.Sprintf("添加客户失败: %s", err), "/customers")
			return
		}
		http.Redirect(w, r, "/customers", http.StatusSeeOther)
	default:
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
	}
}

func (s *Server) handleCustomerDetail(w http.ResponseWriter, r *http.Request) {
	id, ok := parseID(r.URL.Path, "/customers/")
	if !ok {
		http.NotFound(w, r)
		return
	}
	if strings.HasSuffix(r.URL.Path, "/delete") {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if err := s.store.DeleteCustomer(id); err != nil {
			s.renderMessage(w, fmt.Sprintf("删除客户失败: %s", err), "/customers")
			return
		}
		http.Redirect(w, r, "/customers", http.StatusSeeOther)
		return
	}
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}
	customer, err := s.store.GetCustomer(id)
	if err != nil {
		s.renderError(w, err)
		return
	}
	data := PageData{
		Title:    "客户详情",
		Company:  s.cfg.CompanyName,
		Customer: customer,
	}
	s.render(w, "customer_detail.html", data)
}

func (s *Server) handleProducts(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		products, err := s.store.ListProducts()
		if err != nil {
			s.renderError(w, err)
			return
		}
		data := PageData{
			Title:    "产品库",
			Company:  s.cfg.CompanyName,
			Products: products,
		}
		s.render(w, "products.html", data)
	case http.MethodPost:
		if err := r.ParseForm(); err != nil {
			s.renderError(w, err)
			return
		}
		name := strings.TrimSpace(r.FormValue("name"))
		content := strings.TrimSpace(r.FormValue("content"))
		if name == "" {
			s.renderMessage(w, "产品名称不能为空", "/products")
			return
		}
		if err := s.store.CreateProduct(name, content, time.Now()); err != nil {
			s.renderMessage(w, fmt.Sprintf("添加产品失败: %s", err), "/products")
			return
		}
		http.Redirect(w, r, "/products", http.StatusSeeOther)
	default:
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
	}
}

func (s *Server) handleProductDetail(w http.ResponseWriter, r *http.Request) {
	id, ok := parseID(r.URL.Path, "/products/")
	if !ok {
		http.NotFound(w, r)
		return
	}
	if strings.HasSuffix(r.URL.Path, "/delete") {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if err := s.store.DeleteProduct(id); err != nil {
			s.renderMessage(w, fmt.Sprintf("删除产品失败: %s", err), "/products")
			return
		}
		http.Redirect(w, r, "/products", http.StatusSeeOther)
		return
	}
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}
	product, err := s.store.GetProduct(id)
	if err != nil {
		s.renderError(w, err)
		return
	}
	data := PageData{
		Title:   "产品详情",
		Company: s.cfg.CompanyName,
		Product: product,
	}
	s.render(w, "product_detail.html", data)
}

func (s *Server) handleSubscriptions(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		customers, err := s.store.ListCustomers()
		if err != nil {
			s.renderError(w, err)
			return
		}
		products, err := s.store.ListProducts()
		if err != nil {
			s.renderError(w, err)
			return
		}
		subs, err := s.store.ListSubscriptions()
		if err != nil {
			s.renderError(w, err)
			return
		}
		data := PageData{
			Title:         "订阅管理",
			Company:       s.cfg.CompanyName,
			Customers:     customers,
			Products:      products,
			Subscriptions: subs,
		}
		s.render(w, "subscriptions.html", data)
	case http.MethodPost:
		if err := r.ParseForm(); err != nil {
			s.renderError(w, err)
			return
		}
		customerID, _ := strconv.Atoi(r.FormValue("customer_id"))
		productID, _ := strconv.Atoi(r.FormValue("product_id"))
		expiresAt := strings.TrimSpace(r.FormValue("expires_at"))
		note := strings.TrimSpace(r.FormValue("note"))
		if customerID == 0 || productID == 0 || expiresAt == "" {
			s.renderMessage(w, "客户、产品、到期日不能为空", "/subscriptions")
			return
		}
		if err := s.store.CreateSubscription(customerID, productID, expiresAt, note, time.Now()); err != nil {
			s.renderMessage(w, fmt.Sprintf("创建订阅失败: %s", err), "/subscriptions")
			return
		}
		http.Redirect(w, r, "/subscriptions", http.StatusSeeOther)
	default:
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
	}
}

func (s *Server) handleSubscriptionDetail(w http.ResponseWriter, r *http.Request) {
	id, ok := parseID(r.URL.Path, "/subscriptions/")
	if !ok {
		http.NotFound(w, r)
		return
	}
	switch {
	case strings.HasSuffix(r.URL.Path, "/delete"):
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if err := s.store.DeleteSubscription(id); err != nil {
			s.renderMessage(w, fmt.Sprintf("删除订阅失败: %s", err), "/subscriptions")
			return
		}
		http.Redirect(w, r, "/subscriptions", http.StatusSeeOther)
	case strings.HasSuffix(r.URL.Path, "/update"):
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if err := r.ParseForm(); err != nil {
			s.renderError(w, err)
			return
		}
		expiresAt := strings.TrimSpace(r.FormValue("expires_at"))
		note := strings.TrimSpace(r.FormValue("note"))
		sendConfirm := r.FormValue("send_confirm") == "1"
		before, err := s.store.GetSubscription(id)
		if err != nil {
			s.renderError(w, err)
			return
		}
		if err := s.store.UpdateSubscription(id, expiresAt, note); err != nil {
			s.renderMessage(w, fmt.Sprintf("更新订阅失败: %s", err), fmt.Sprintf("/subscriptions/%d", id))
			return
		}
		if sendConfirm && s.mailer.Enabled() {
			after, _ := s.store.GetSubscription(id)
			_ = s.reminder.SendRenewalConfirm(after, before.ExpiresAt, expiresAt)
		}
		http.Redirect(w, r, fmt.Sprintf("/subscriptions/%d", id), http.StatusSeeOther)
	default:
		if r.Method != http.MethodGet {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		subscription, err := s.store.GetSubscription(id)
		if err != nil {
			s.renderError(w, err)
			return
		}
		data := PageData{
			Title:        "订阅详情",
			Company:      s.cfg.CompanyName,
			Subscription: subscription,
		}
		s.render(w, "subscription_detail.html", data)
	}
}

func (s *Server) handleSettings(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/settings" {
		http.NotFound(w, r)
		return
	}
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}
	rules, _ := s.store.GetRules()
	template, _ := s.store.GetTemplate()
	renewalTemplate, _ := s.store.GetRenewalTemplate()
	data := PageData{
		Title:           "规则与模板",
		Company:         s.cfg.CompanyName,
		Rules:           rules,
		RulesInput:      joinInts(rules),
		Template:        template,
		RenewalTemplate: renewalTemplate,
	}
	s.render(w, "settings.html", data)
}

func (s *Server) handleSettingsActions(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case "/settings/rules":
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if err := r.ParseForm(); err != nil {
			s.renderError(w, err)
			return
		}
		rules, err := reminder.ParseRules(r.FormValue("rules"))
		if err != nil {
			s.renderMessage(w, err.Error(), "/settings")
			return
		}
		if err := s.store.UpdateRules(rules); err != nil {
			s.renderMessage(w, fmt.Sprintf("更新规则失败: %s", err), "/settings")
			return
		}
		http.Redirect(w, r, "/settings", http.StatusSeeOther)
	case "/settings/template":
		s.saveTemplate(w, r, false)
	case "/settings/renewal-template":
		s.saveTemplate(w, r, true)
	default:
		http.NotFound(w, r)
	}
}

func (s *Server) saveTemplate(w http.ResponseWriter, r *http.Request, renewal bool) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if err := r.ParseForm(); err != nil {
		s.renderError(w, err)
		return
	}
	subject := r.FormValue("subject")
	htmlBody := r.FormValue("html")
	tpl := db.Template{Subject: subject, HTML: htmlBody}
	var err error
	if renewal {
		err = s.store.UpdateRenewalTemplate(tpl)
	} else {
		err = s.store.UpdateTemplate(tpl)
	}
	if err != nil {
		s.renderMessage(w, fmt.Sprintf("保存模板失败: %s", err), "/settings")
		return
	}
	http.Redirect(w, r, "/settings", http.StatusSeeOther)
}

func (s *Server) handleScan(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if err := r.ParseForm(); err != nil {
		s.renderError(w, err)
		return
	}
	threshold, _ := strconv.Atoi(r.FormValue("threshold"))
	result, err := s.reminder.SendNow(threshold, time.Now())
	if err != nil {
		s.renderMessage(w, fmt.Sprintf("扫描失败: %s", err), "/")
		return
	}
	msg := fmt.Sprintf("扫描完成：总计 %d，发送 %d，跳过 %d，失败 %d", result.Total, result.Sent, result.Skipped, result.Failed)
	s.renderMessage(w, msg, "/")
}

func (s *Server) render(w http.ResponseWriter, page string, data PageData) {
	data.Title = strings.TrimSpace(data.Title)
	data.Company = s.cfg.CompanyName
	tpl, err := template.New("layout.html").ParseFS(assetsFS, "templates/layout.html", path.Join("templates", page))
	if err != nil {
		s.renderError(w, err)
		return
	}
	err = tpl.ExecuteTemplate(w, "layout", data)
	if err != nil {
		s.renderError(w, err)
	}
}

func (s *Server) renderMessage(w http.ResponseWriter, msg, redirect string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	fmt.Fprintf(w, `<meta http-equiv="refresh" content="1; url=%s"><div class="alert">%s</div>`, redirect, template.HTMLEscapeString(msg))
}

func (s *Server) renderError(w http.ResponseWriter, err error) {
	w.WriteHeader(http.StatusInternalServerError)
	io.WriteString(w, fmt.Sprintf("错误: %s", err))
}

func parseID(fullPath, prefix string) (int, bool) {
	trimmed := strings.TrimPrefix(fullPath, prefix)
	trimmed = strings.TrimSuffix(trimmed, "/delete")
	trimmed = strings.TrimSuffix(trimmed, "/update")
	trimmed = strings.TrimSuffix(trimmed, "/")
	if trimmed == "" {
		return 0, false
	}
	parts := strings.Split(trimmed, "/")
	id, err := strconv.Atoi(parts[0])
	return id, err == nil
}

func joinInts(values []int) string {
	var out []string
	for _, v := range values {
		out = append(out, fmt.Sprintf("%d", v))
	}
	return strings.Join(out, ",")
}

func maxInt(values []int) int {
	if len(values) == 0 {
		return 0
	}
	max := values[0]
	for _, v := range values {
		if v > max {
			max = v
		}
	}
	return max
}

func renderText(tpl string, data any) (string, error) {
	t, err := template.New("subject").Parse(tpl)
	if err != nil {
		return "", err
	}
	var buf strings.Builder
	if err := t.Execute(&buf, data); err != nil {
		return "", err
	}
	return buf.String(), nil
}

func renderHTML(tpl string, data any) (string, error) {
	t, err := template.New("html").Parse(tpl)
	if err != nil {
		return "", err
	}
	var buf strings.Builder
	if err := t.Execute(&buf, data); err != nil {
		return "", err
	}
	return buf.String(), nil
}
