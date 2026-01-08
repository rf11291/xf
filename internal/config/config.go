package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	Addr                string
	DatabasePath        string
	CompanyName         string
	ScanIntervalMinutes int
	TimeZone            *time.Location
	AdminUser           string
	AdminPass           string
	SMTPHost            string
	SMTPPort            int
	SMTPUser            string
	SMTPPass            string
	SMTPFrom            string
}

func Load() (Config, error) {
	cfg := Config{
		Addr:                getEnv("APP_ADDR", ":8080"),
		DatabasePath:        getEnv("DATABASE_PATH", "./data/panel.db"),
		CompanyName:         getEnv("COMPANY_NAME", "YourCompany"),
		ScanIntervalMinutes: getEnvInt("SCAN_INTERVAL_MINUTES", 15),
		AdminUser:           getEnv("ADMIN_USER", "admin"),
		AdminPass:           getEnv("ADMIN_PASS", "admin123"),
		SMTPHost:            getEnv("SMTP_HOST", ""),
		SMTPPort:            getEnvInt("SMTP_PORT", 587),
		SMTPUser:            getEnv("SMTP_USER", ""),
		SMTPPass:            getEnv("SMTP_PASS", ""),
		SMTPFrom:            getEnv("SMTP_FROM", ""),
	}

	tzName := getEnv("TZ", "Asia/Shanghai")
	loc, err := time.LoadLocation(tzName)
	if err != nil {
		return cfg, fmt.Errorf("invalid TZ %q: %w", tzName, err)
	}
	cfg.TimeZone = loc
	return cfg, nil
}

func getEnv(key, fallback string) string {
	val := strings.TrimSpace(os.Getenv(key))
	if val == "" {
		return fallback
	}
	return stripQuotes(val)
}

func getEnvInt(key string, fallback int) int {
	val := strings.TrimSpace(os.Getenv(key))
	if val == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(stripQuotes(val))
	if err != nil {
		return fallback
	}
	return parsed
}

func stripQuotes(val string) string {
	if len(val) < 2 {
		return val
	}
	if (val[0] == '"' && val[len(val)-1] == '"') || (val[0] == '\'' && val[len(val)-1] == '\'') {
		return val[1 : len(val)-1]
	}
	return val
}
