#!/bin/bash
# GitLab Code Review Tool - Installation Script for CentOS
# Run as root or with sudo

set -e

INSTALL_DIR="/opt/gitlab-code-review-tool"
SERVICE_USER="gitlab-review"
SERVICE_GROUP="gitlab-review"

echo "=========================================="
echo "GitLab Code Review Tool - Installer"
echo "=========================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root or with sudo"
    exit 1
fi

# Install system dependencies
echo "Installing system dependencies..."
yum install -y python39 python39-pip python39-devel gcc

# Create service user
if ! id "$SERVICE_USER" &>/dev/null; then
    echo "Creating service user: $SERVICE_USER"
    useradd -r -s /bin/false $SERVICE_USER
fi

# Create installation directory
echo "Creating installation directory: $INSTALL_DIR"
mkdir -p $INSTALL_DIR

# Copy application files
echo "Copying application files..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cp -r $PROJECT_DIR/app $INSTALL_DIR/
cp -r $PROJECT_DIR/tests $INSTALL_DIR/
cp $PROJECT_DIR/run.py $INSTALL_DIR/
cp $PROJECT_DIR/requirements.txt $INSTALL_DIR/
cp $PROJECT_DIR/pyproject.toml $INSTALL_DIR/ 2>/dev/null || true

# Create data directories
mkdir -p $INSTALL_DIR/data/{logs,reports,config}
mkdir -p $INSTALL_DIR/config

# Create virtual environment
echo "Creating virtual environment..."
cd $INSTALL_DIR
python3.9 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Generate secret key
SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')

# Update systemd service file with correct paths
sed -i "s|/opt/gitlab-code-review-tool|$INSTALL_DIR|g" $PROJECT_DIR/scripts/gitlab-code-review.service
sed -i "s|your-secret-key-here|$SECRET_KEY|g" $PROJECT_DIR/scripts/gitlab-code-review.service

# Install systemd service
echo "Installing systemd service..."
cp $PROJECT_DIR/scripts/gitlab-code-review.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable gitlab-code-review

# Set permissions
echo "Setting permissions..."
chown -R $SERVICE_USER:$SERVICE_GROUP $INSTALL_DIR
chmod 600 $INSTALL_DIR/config/admin.yaml 2>/dev/null || true

echo "=========================================="
echo "Installation complete!"
echo "=========================================="
echo ""
echo "To start the service:"
echo "  systemctl start gitlab-code-review"
echo ""
echo "To check status:"
echo "  systemctl status gitlab-code-review"
echo ""
echo "To view logs:"
echo "  journalctl -u gitlab-code-review -f"
echo ""
echo "The application will be available at: http://localhost:5001"
echo ""
echo "IMPORTANT: After starting, check the logs for the initial admin password!"
echo "=========================================="
