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

# Part 1

In Part 2 of this project, you will run eight different throughput-oriented (“batch”) workloads from
the PARSEC (and SPLASH-2x) benchmark suite: barnes, blackscholes, canneal, freqmine, radix,
streamcluster and vips. You will first explore each workload’s sensitivity to resource interference
using iBench on a small 2 core VM (e2-standard-2). This is somewhat similar to what you did
in Part 1 for memcache. Next, you will investigate how each workload benefits from parallelism by
measuring the performance of each job with 1, 2, 4, 8 threads on a large 8 core VM (e2-standard-8).
In the latter scenario, no interference is used.

Follow the setup instructions below to deploy a Google Cloud cluster and run the batch applications. Please find the project report template containing the questions and free space you should
use on Moodle.

## PARSEC Behavior with Interference

For the first half of Part 2, you will have to set up a single node cluster consisting of a VM with 2
CPUs. For this, we will employ kops and make use of the part2a.yaml file (make sure to update
the file with values for your GCP project and configBase):

```sh
export KOPS_STATE_STORE=<your-gcp-state-store>
PROJECT=`gcloud config get-value project`
kops create -f part2a.yaml
kops update cluster part2a.k8s.local --yes --admin
kops validate cluster --wait 10m
kubectl get nodes -o wide
```

If successful, you should see something like this

```

```

Now you should be able to connect to the parsec-server VM using ssh:

```sh
ssh -i ~/.ssh/cloud-computing ubuntu@35.234.110.58
```

To make sure that the jobs can be scheduled successfully, run the following command in order to
assign the appropriate label to the parsec node (replace the <parsec-server-name> with the name
of the parsec server observed in the output of the kubectl get nodes command):

```sh
kubectl label nodes <parsec-server-name> cca-project-nodetype=parsec
```

For this part of the study we will sometimes require to set up some form of interference, and also
deploy a job. For this example, we will use the PARSEC barnes job together with iBench CPU
interference. Here is where we will use kubectl together with some of the yaml files we provide.
The following code snippet spins up the interference, and runs the PARSEC barnes job:
```sh
kubectl create -f interference/ibench-cpu.yaml # Wait for interference to start
kubectl create -f parsec-benchmarks/part2a/parsec-barnes.yaml
```

Please note that, for Part 2a, you should use the job templates contained in the parsec-benchmarks/part2a
folder. blackscholes, canneal, streamcluster and freqmine use the simlarge dataset, while
barnes, radix, and vips use the native dataset. This is specified in the startup command for the
container in the template file.
Make sure that the interference has properly started before running the PARSEC job. One way to
see if the interference and the PARSEC job has started refers to ssh-ing into the VM and using the
htop command to inspect running processes.

You can get information on submitted jobs using:
```sh
kubectl get jobs
```
In order to get the output of the PARSEC job, you will have to collect the logs of its pods. To do
so, you will have to run the following commands.

```sh
kubectl logs $(kubectl get pods --selector=job-name=<job_name> --output=jsonpath='{.items[*].metadata.name}')
```

Note that the job name needs to match the one you get from kubectl get jobs.
Run experiments sequentially and wait for one benchmark to finish before you spin
up the next one. Once you are done with running one experiment, make sure to terminate the
started jobs. You can terminate them all together using:

```sh
kubectl delete jobs --all
kubectl delete pods --all
```

Alternatively, you can do so one-by-one using the following command:

```sh
kubectl delete job <job_name>
```

IMPORTANT: you must delete your cluster when you are not using it! Otherwise,
you will easily use up all of your cloud credits! When you are ready to work on the project,
you can easily re-launch the cluster with the instructions above. To delete your cluster, use the
command:
```sh
kops delete cluster part2a.k8s.local --yes
```

## PARSEC Parallel Behavior

For the second half of Part 2, you will have to look into the parallel behavior of PARSEC, more
specifically, how does the performance of various jobs in PARSEC change as more threads are added
(more specifically 1, 2, 4 and 8 threads). For this part of the study, no interference is used.
You will first have to spawn a cluster as in section 2.1.1, however, this time use the part2b.yaml
file we provided (make sure to update the file with values for your GCP project and configBase).
Once more, this will be a single node cluster with an 8 CPU VM. You will have to vary the number
of threads for each PARSEC job. To do so, change the value of the -n parameter in the relevant yaml
files. The corresponding .yaml files are in parsec-benchmarks/part2b folder of the GitHub repo.
Note that, for Part 2b, all of the jobs use the native dataset.
Other relevant instructions for this task can be found in section 2.1.1.

IMPORTANT: you must delete your cluster when you are not using it! Otherwise,
you will easily use up all of your cloud credits! When you are ready to work on the project,
you can easily re-launch the cluster with the instructions above. To delete your cluster, use the
command:

```sh
$ kops delete cluster part2b.k8s.local --yes
```

