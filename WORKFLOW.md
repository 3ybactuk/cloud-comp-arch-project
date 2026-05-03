# Part 1

## Deploying a cluster using kops

At this point you will deploy a cluster using kops. First of all you will need to create an empty
bucket to store the configuration for your clusters. Do this by running:

```bash
gsutil mb gs://cca-eth-2026-group-087-ethzid/
```
... where ethzid is your ETH username. Then run the following
command to have the `KOPS_STATE_STORE` command to your environment for the subsequent steps:

```sh
export KOPS_STATE_STORE=gs://cca-eth-2026-group-087-ethzid/
```

If you open another terminal this and other environmental variables will not be preserved. You can preserve it by adding it with an export command to your .bashrc. You
should substitute the number of your group and your ETH username as before.

For the first part of the exercise you will need a 3 node cluster. Two VMs will have 2 cores. One
of these VMs will be the node where memcached and iBench will be deployed and another will be
used for for the mcperf memcached client which will measure the round-trip latency of memcached
requests. The third VM will have 8 cores and hosts the mcperf client which generates the request
load for the experiments

(setup ssh key as like in the guide)

Once you have created the key, go to lines 16 and 43 of the part1.yaml file (provided in the github
link above) and substitute the placeholder values with your group number and ethzid.
Then run the following commands to create a kubernetes cluster with 1 master and 2 nodes.
```bash
PROJECT=`gcloud config get-value project`
kops create -f part1.yaml
```

We will now add the key as a login key for our nodes. Type the following command:

```sh
kops create secret --name part1.k8s.local sshpublickey admin -i ~/.ssh/cloud-computing.pub
```

At this point, your cluster has not yet been deployed.
The configurations we provide are such that only one student in each group may run and work with
a cluster at a given time. Different group members attempting to deploy clusters at the
same time will run into errors.

The intended workflow is that only one group member should be working on collecting measurements at a given time, and then share their results with the rest of the group. You can then discuss and analyze these measurement results as a group and work together to fill out the report and develop your scheduling policies for parts 3 and 4.

Feel free to experiment by modifying cluster configurations such that multiple clusters can be run at the same time or to allow multiple group members to work with the same cluster simultaneously, however neither of these are necessary.

Keep in mind that deploying multiple clusters will increase your cloud credit consumption, and make sure that you do not compromise each others’ measurements by e.g. running multiple versions of the same jobs or otherwise causing interference if you work on the same cluster simultaneously.

We are ready now to deploy the cluster by typing:

```sh
kops update cluster --name part1.k8s.local --yes --admin
```

Your cluster should need around 5-10 minutes to be deployed. You can validate this by typing:

```sh
kops validate cluster --wait 10m
```

The command will terminate when your cluster is ready to use. If you get a connection refused
or cluster not yet healthy messages, wait while the previous command automatically retries.
When the command completes, you can type:

```sh
kubectl get nodes -o wide
```

## Running memcached and the mcperf load generator

To launch memcached using Kubernetes, run the following:

```sh
kubectl create -f memcache-t1-cpuset.yaml
kubectl expose pod some-memcached --name some-memcached-11211 --type LoadBalancer --port 11211 --protocol TCP

sleep 60

kubectl get service some-memcached-11211
```

Then run the following:

```sh
kubectl get pods -o wide
```

If the memcache doesn't get scheduled, use:
```bash
kubectl label node memcache-server-<ID> cca-project-nodetype=memcached  
```

The output for `get pods` should look like:

```
NAME             READY   STATUS    RESTARTS   AGE   IP           NODE
some-memcached   1/1     Running   0          83s   100.96.3.2   memcache-server-bh45
```

Use the IP address above (100.96.3.3 in this example) as the `MEMCACHED_IP` in the remaining
instructions. Now ssh into both the `client-agent` and `client-measure` VMs and run the following
commands to compile the mcperf memcached load generator:

```sh
sudo apt-get update
sudo apt-get install libevent-dev libzmq3-dev git make g++ --yes
sudo sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources
sudo apt-get update
sudo apt-get build-dep memcached --yes
cd && git clone https://github.com/shaygalon/memcache-perf.git
cd memcache-perf
git checkout 0afbe9b
make
```

On the client-agent VM, you should now run the following command to launch the mcperf
memcached client load agent with 8 threads:

```sh
./mcperf -T 8 -A
```

On the client-measure VM, run the following command to first load the memcached database
with key-value pairs and then query memcached with throughput increasing from 5000 queries per
second (QPS) to 80000 QPS in increments of 5000:

```bash
./mcperf -s MEMCACHED_IP --loadonly
# ./mcperf -s 100.96.3.2 --loadonly

./mcperf -s MEMCACHED_IP -a INTERNAL_AGENT_IP --noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 5 -w 2 --scan 5000:80000:5000
# ./mcperf -s 100.96.3.2 -a 10.0.16.6 --noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 5 -w 2 --scan 5000:80000:5000
```

### Introducing Resource Interference

Now we are going to introduce different types of resource interference with iBench microbenchmarks.
Run the following commands:

```bash
kubectl create -f interference/ibench-cpu.yaml
```

This will launch a CPU interference microbenchmark. You can check it is running correctly with:

```bash
kubectl get pods -o wide
```

(wait until READY 1/1 and STATUS Running shows before starting a run).
When you have finished collecting memcached performance measurements with CPU interference,
you should kill the job by running:

```bash
kubectl delete pods ibench-cpu
```

You can apply the above three steps for any of the six ibench-cpu, ibench-l1d, ibench-l1i, ibench-l2, ibench-llc, and ibench-membw interference microbenchmarks. 

For Part 1 you will perform experiments to investigate the effect of the different types of interference. After now having followed this tutorial, you are able to run those experiments. First, start with reading the information of what to run for Part 1 in the project report template.

### Deleting your cluster
**IMPORTANT: you must delete your cluster when you are not using it!**

Otherwise,
you will easily use up all of your cloud credits! When you are ready to work on the project,
you can easily re-launch the cluster with the instructions above.
To delete your cluster, run on your local machine the command:

```bash
kops delete cluster part1.k8s.local --yes
```

If you encounter an API permissions error, make sure to enable the IAM API by visiting https://console.cloud.google.com/apis/api/iam.googleapis.com/overview?project=<yourprojecthere>
Make sure to replace the placeholder with your project name.
