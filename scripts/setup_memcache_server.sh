#!/usr/bin/env bash
# Run on the memcache-server VM.
# ./setup_memcache_server.sh <MEMCACHED_INTERNAL_IP> <MEMCACHED_THREADS>

set -euo pipefail

MEMCACHED_IP=${1:?Usage: $0 MEMCACHED_INTERNAL_IP THREADS}
MEMCACHED_THREADS=${2:-2}

sudo apt update -q
sudo apt install -y -q memcached libmemcached-tools docker.io

sudo usermod -aG docker "$USER" || true
echo "Docker: $(docker --version)"

sudo tee /etc/memcached.conf > /dev/null <<EOF
-m 1024
-p 11211
-u memcache
-l $MEMCACHED_IP
-t $MEMCACHED_THREADS
EOF

sudo systemctl restart memcached
sudo systemctl enable memcached
sleep 2
echo "Memcached status:"
sudo systemctl status memcached

GO_VERSION=1.25.0
wget -q "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" -O /tmp/go.tar.gz
sudo rm -rf /usr/local/go
sudo tar -C /usr/local -xzf /tmp/go.tar.gz
echo 'export PATH=$PATH:/usr/local/go/bin' >> ~/.bashrc
export PATH=$PATH:/usr/local/go/bin

echo "Go: $(go version)"

mkdir -p ~/controller
cp -r /path/to/controller/* ~/controller/ 2>/dev/null || true  # adjust path after scp
cd ~/controller
go mod download
go build -o controller .
echo "Controller built: $(ls -lh controller)"

echo "Next steps:"
echo "1. Setup clients with setup_client_mcperf.sh"
echo "2. Start mcperf agent on client-agent: ./mcperf -T 8 -A"
echo "3. On client-measure: run scripts/run_experiment.sh"
