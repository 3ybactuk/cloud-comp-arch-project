#!/bin/bash
# setup_mcperf.sh — запускай после kops validate + kubectl label nodes
ZONE="europe-west1-b"
KEY="$HOME/.ssh/cloud-computing"

install_on_node() {
    local NODE=$1
    echo "=== Installing mcperf on $NODE ==="
    gcloud compute ssh --ssh-key-file "$KEY" "ubuntu@$NODE" \
        --zone "$ZONE" \
        --command 'sudo apt-get update -q && sudo apt-get install -y libevent-dev libzmq3-dev git make g++ gengetopt && sudo apt-get build-dep -y memcached && cd ~ && git clone https://github.com/eth-easl/memcache-perf-dynamic.git && cd memcache-perf-dynamic && gengetopt < cmdline.ggo && make'
    echo "=== Done: $NODE ==="
}

for NODE in $(kubectl get nodes -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | grep -E 'client-agent|client-measure'); do
    install_on_node "$NODE" &
done
wait
echo "All nodes done."
