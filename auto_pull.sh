cat << 'EOF' > auto_pull.sh
#!/bin/bash
set -e
cd "$(dirname "$0")"

git fetch origin main
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" != "$REMOTE" ]; then
   git pull origin main
fi
EOF
chmod +x auto_pull.sh
