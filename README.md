# Paperless-ngx Bulletproof

A modern, backup-centric management system for **Paperless-ngx** with multi-instance support, comprehensive backup automation, and enhanced diagnostics.

> **🚀 Recent Enhancements (September 2025)**
> - **Enhanced multi-instance diagnostics** that work across all instances automatically
> - **Complete Paperless-ngx compliance** with health checks, service dependencies, and proper startup ordering
> - **Robust Traefik configuration** with automatic network creation and HTTPS setup guidance
> - **Streamlined codebase** with all duplicate/legacy code removed
> - **Comprehensive backup system** including all configuration files and scripts

## ✨ Features

### 🏗️ Multi-Instance Management
- Manage multiple Paperless-ngx instances from a unified dashboard
- Each instance operates independently with its own ports, domains, and backup schedules
- Cross-instance backup restoration and cloning capabilities

### ☁️ Universal Cloud Storage
- Works with **any** rclone-supported cloud provider (pCloud, Google Drive, Dropbox, OneDrive, S3, and 40+ others)
- Intelligent OAuth authentication with automatic region detection
- WebDAV fallback for enhanced compatibility

### 🛡️ Comprehensive Backup System
- **Three backup modes**: Full, Incremental, and Archive with automatic retention
- Automated backup scheduling with cron integration
- Backup integrity verification and health checks
- Cross-instance snapshot cloning and restoration

### 🎨 Modern CLI Experience
- Beautiful, intuitive command-line interface with visual feedback
- Numbered menu choices for consistent navigation
- Enhanced diagnostic tools with comprehensive system health monitoring
- Auto-start capabilities for new instances

### 🔒 Production-Ready Infrastructure
- Docker + Docker Compose with all essential services (PostgreSQL, Redis, Gotenberg, Tika)
- Optional Traefik reverse proxy with Let's Encrypt HTTPS
- Dynamic port allocation to prevent conflicts
- Comprehensive error handling and validation

---

## 🚀 Quick Start

### Installation

**Note**: Use the correct repository name `obidose-paperless-ngx-bulletproof` (not just `paperless-ngx-bulletproof`)

```bash
# Download and run the installer
curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/install.py | sudo python3
```

The installer will:
1. Install system prerequisites (Docker, Docker Compose, rclone)
2. Download and install the bulletproof CLI
3. Launch the interactive setup wizard

### First-Time Setup

After installation, the bulletproof CLI will guide you through:

1. **Cloud Storage Configuration** - Set up your preferred cloud provider for backups
2. **Instance Creation** - Create your first Paperless-ngx instance with guided configuration
3. **Backup Schedule Setup** - Configure automated backup timing and retention

---

## 🎯 Multi-Instance Dashboard

Running `bulletproof` shows a unified dashboard of all instances:

```
╔════════════════════════════════════════════════════════════╗
║                 Paperless-ngx Instances                   ║
╚════════════════════════════════════════════════════════════╝

# │ NAME           │ STATUS   │ BACKUP SCHEDULE
──┼────────────────┼──────────┼─────────────────────────────────
1 │ production     │ ● up     │ Full: every Sunday at 03:30, Incr: daily at 00:00
2 │ testing        │ ○ down   │ Full: every Saturday at 02:00, Incr: every 6h
3 │ archive        │ ● up     │ Full: monthly 1 at 04:00, Archive: monthly cleanup

┌─ Multi-Instance Actions ─────────────────────────────────┐
│ → 1) Manage instances                                    │
│ → 2) Create new instance                                 │
│ → 3) Start all instances                                 │
│ → 4) Stop all instances                                  │
│ → 5) Delete all instances                                │
│ → 6) Explore backups                                     │
│ → 7) Configure cloud storage                             │
│ → 8) System diagnostics                                  │
│ → 9) Connection troubleshooting                          │
├─────────────────────────────────────────────────────────┤
│ ◦ 0) Quit                                                │
└─────────────────────────────────────────────────────────┘
```

## 🔧 Per-Instance Management

Select any instance for detailed management:

```
┌─ Instance Management ────────────────────────────────────┐
│ → 1) Start instance                                      │
│ → 2) Stop instance                                       │
│ → 3) View logs                                           │
│ → 4) Backup (incremental)                                │
│ → 5) Backup (full)                                       │
│ → 6) Backup (archive)                                    │
│ → 7) List snapshots                                      │
│ → 8) Restore from snapshot                               │
│ → 9) Upgrade instance                                    │
│ → 10) System diagnostics                                 │
│ → 11) Configure schedule                                 │
│ → 12) Connection troubleshooting                         │
├─────────────────────────────────────────────────────────┤
│ ◦ 0) Quit                                                │
└─────────────────────────────────────────────────────────┘
```

## 📦 Backup System

### Three Backup Modes

- **📱 Incremental** - Fast daily backups of changes only
- **📦 Full** - Complete weekly snapshots for reliability
- **🗄️ Archive** - Monthly long-term storage with automatic cleanup

### Backup Independence

Each instance maintains its own backup chain at:
```
pcloud:backups/paperless/${INSTANCE_NAME}/
```

This allows you to:
- Run different backup schedules per instance
- Restore instances selectively
- Share backups between different servers
- Maintain separate retention policies

### Backup Integrity Verification

The backup explorer includes comprehensive integrity checking:

```
┌─ Backup Explorer ────────────────────────────────────────┐
│ → 1) List all remote instances                           │
│ → 2) Show snapshots for instance                         │
│ → 3) Verify snapshot                                     │
│ → 4) Verify all snapshots for instance                   │
├─────────────────────────────────────────────────────────┤
│ ◦ 0) Quit                                                │
└─────────────────────────────────────────────────────────┘
```

Verification includes:
- ✓ Manifest file validation with YAML parsing
- ✓ Required files verification (database.sql, media.tar)
- ✓ File size analysis and zero-byte detection
- ✓ Database file structure validation
- ✓ Detailed integrity scoring with summary reports

---

## 🏗️ Architecture

The system uses a clean modular architecture:

```
tools/
├── bulletproof.py      # Main CLI orchestration and multi-instance management
├── ui.py              # Visual interface, colors, formatting, user input
├── cloud_storage.py   # Universal cloud storage setup and rclone management
├── instance.py        # Instance lifecycle, discovery, and operations
└── backup_restore.py  # Backup creation, snapshot management, and restore
```

### Module Responsibilities

- **`bulletproof.py`** - Main CLI with multi-instance dashboard, menu systems, and command routing
- **`ui.py`** - Consistent visual experience with colors, icons, tables, and interactive prompts
- **`cloud_storage.py`** - Universal cloud storage with OAuth, region detection, and provider support
- **`instance.py`** - Instance management, Docker operations, and configuration handling
- **`backup_restore.py`** - Comprehensive backup workflows, integrity verification, and restoration

---

## 🚀 Installation Requirements

- **Ubuntu 22.04** or **24.04**
- **Root access** (or prefix commands with `sudo`)
- **Internet connection** for downloading components
- **Cloud storage account** (pCloud, Google Drive, Dropbox, etc.)

> **DNS Configuration**: If using Traefik + HTTPS, ensure DNS points to your host so Let's Encrypt can issue certificates.

---

## 🔍 Enhanced Diagnostics

The system includes comprehensive diagnostic tools accessible from any instance:

```
┌─ System Diagnostics ─────────────────────────────────────┐
│ Environment Configuration:                               │
│ - INSTANCE_NAME: production [ok]                         │
│ - STACK_DIR: /home/docker/production-setup [ok]         │
│ - COMPOSE_FILE: /home/docker/production-setup/docker-compose.yml [ok] │
│                                                          │
│ System Dependencies:                                     │
│ ✓ Docker daemon: Available                              │
│ ✓ Docker Compose: Available                             │
│ ✓ Rclone: Available                                     │
│                                                          │
│ Container Status:                                        │
│ NAME                IMAGE               STATUS           │
│ production-db       postgres:15-alpine Up 2 hours       │
│ production-redis    redis:7-alpine     Up 2 hours       │
│ production-paperless ghcr.io/paperless-ngx/paperless-ngx:latest Up 2 hours │
│                                                          │
│ Port Availability:                                       │
│ ✓ Port 8000: Service responding                         │
│   Paperless-ngx available at: http://localhost:8000     │
│                                                          │
│ ✓ All diagnostics completed!                            │
└─────────────────────────────────────────────────────────┘
```

---

## 🤝 Contributing

The modular architecture makes contributions straightforward:

- **Add cloud providers** in `cloud_storage.py`
- **Enhance UI elements** in `ui.py`
- **Extend backup features** in `backup_restore.py`
- **Improve instance management** in `instance.py`

### Development Setup

```bash
git clone https://github.com/obidose/obidose-paperless-ngx-bulletproof.git
cd obidose-paperless-ngx-bulletproof/tools
python bulletproof.py --help
```

---

## 📋 What Gets Installed

The installer sets up a complete Paperless-ngx environment:

### Docker Services
- **PostgreSQL 15** - Primary database
- **Redis 7** - Task queue and caching
- **Paperless-ngx** - Document management system
- **Gotenberg** - Document conversion service
- **Tika** - Text extraction service
- **Traefik** (optional) - Reverse proxy with automatic HTTPS

### Directory Structure
```
/home/docker/
├── {instance-name}/           # Data directory
│   ├── data/                  # Paperless application data
│   ├── media/                 # Document storage
│   ├── export/                # Export directory
│   └── pgdata/                # PostgreSQL data
└── {instance-name}-setup/     # Stack directory
    ├── docker-compose.yml     # Container definitions
    ├── .env                   # Environment configuration
    ├── init.sh                # Initialization script
    └── maintenance.sh         # Maintenance utilities
```

### Cloud Storage
- **rclone** configured with your preferred provider
- **OAuth authentication** with automatic region detection
- **Backup directory** structure in cloud storage

### System Integration
- **bulletproof** CLI available system-wide
- **Automated backups** via cron (optional)
- **Log rotation** and monitoring capabilities

---

## 📄 License

MIT License - see LICENSE file for details.

---

**Made with ❤️ for the Paperless-ngx community**

*Bulletproof: Because your documents deserve bulletproof protection.*