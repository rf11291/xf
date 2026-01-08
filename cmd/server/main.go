package main

import (
	"log"
	"net/http"
	"time"

	"xf/internal/config"
	"xf/internal/db"
	"xf/internal/email"
	"xf/internal/reminder"
	"xf/internal/web"
)

func main() {
	cfg, err := config.Load()
	if err != nil {
		log.Fatalf("config error: %v", err)
	}

	store, err := db.Open(cfg.DatabasePath)
	if err != nil {
		log.Fatalf("db error: %v", err)
	}
	defer store.Close()

	mailer := email.Mailer{
		Host: cfg.SMTPHost,
		Port: cfg.SMTPPort,
		User: cfg.SMTPUser,
		Pass: cfg.SMTPPass,
		From: cfg.SMTPFrom,
	}

	server, err := web.NewServer(cfg, store, mailer)
	if err != nil {
		log.Fatalf("server error: %v", err)
	}

	startScheduler(cfg, store, mailer)

	log.Printf("renewal panel listening on %s", cfg.Addr)
	if err := http.ListenAndServe(cfg.Addr, server.Routes()); err != nil {
		log.Fatalf("listen error: %v", err)
	}
}

func startScheduler(cfg config.Config, store *db.Store, mailer email.Mailer) {
	ticker := time.NewTicker(time.Duration(cfg.ScanIntervalMinutes) * time.Minute)
	renderer := web.TemplateRenderer{}
	service := reminder.Service{
		Store:    store,
		Mailer:   mailer,
		Company:  cfg.CompanyName,
		Location: cfg.TimeZone,
		Render:   renderer,
	}
	go func() {
		for range ticker.C {
			if !mailer.Enabled() {
				continue
			}
			if _, err := service.ScanAndSend(time.Now()); err != nil {
				log.Printf("scan error: %v", err)
			}
		}
	}()
}
