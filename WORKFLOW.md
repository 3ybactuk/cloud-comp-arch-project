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

# Part 2

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

# Part 3

In Part 3 of the project, you will combine the input gained from the previous two parts. You will now
co-schedule the latency-critical memcached application from Part 1 and all seven batch applications
from Part 2 in a heterogeneous cluster, consisting of VMs with a different number of cores. Your
cluster will consist of a VM for the Kubernetes master (same as in Part 1), 3 VMs for the mcperf
clients (2 agents and 1 measure machine), and 2 heterogeneous VMs (node-a-8core with 8 cores
and node-b-4core with 4 cores) which are used to run memcached and the batch applications.
Note that these VMs also have different configurations (as you can see in the part3.yaml file):
node-a-8core is of type e2-standard-8, node-b-4core is of type n2d-highcpu-4. The number
of CPUs, the CPU platform, and the amount of memory differ in these VMs, which is something
that you should take into account when designing your scheduling policy.

Your goal is to design a scheduling policy that will minimize the time it takes for all seven batch
workloads to complete (their makespan), while guaranteeing a tail latency service level objective
(SLO) for the long-running memcached service. It might be helpful to take into account the characteristics of the batch applications you noted in Part 2 of the project(e.g. speedup across cores, total
runtime, etc.). For this part of the project, the memcached service will receive requests from the
client at a steady rate, and you will measure the request tail latency. Your scheduling policy should
minimize the makespan of all batch applications, without violating a strict service level objective for memcached of 1 ms 95th percentile latency at 30K QPS. You also must ensure that
all seven batch applications complete successfully, as jobs may abort due to errors (e.g. out of
memory). Use the native dataset size for all batch applications. At every point in time, you
must use as many resources of your cluster as possible.

When designing and implementing your scheduling policy, you will experiment with different collocation and resource management strategies using Kubernetes mechanisms. Utilize the knowledge
you gained about the performance characteristics of each application in Parts 1 and 2 of the project.
This information will help you decide the degree of parallelism you should run each workload with,
and which applications you should collocate on shared resources.

Please find the project report template containing the questions and free space you should use to
enter your results on Moodle.

You may modify the YAML files provided, write a script for controlling the batch applications, or
apply any other techniques you choose, as long as you describe them clearly in your report. You can
choose which jobs to collocate, which degree of parallelism to use, and when to launch particular
batch applications. You may use any Kubernetes mechanism you wish to implement your scheduling
policy. You may find node/pod affinity and/or resource requests/limits particularly useful. You
also may want to use taskset in the container command arguments to pin containers to certain
CPU cores of a node. Keep in mind that a job may fail due to the lack of resources. You can use
kubectl describe jobs to monitor jobs.

## Setup

Run the following command to create a Kubernetes cluster with 1 master and 5 nodes. Make sure
to update the part3.yaml file with the name of your project and your ConfigBase.
```sh
$ export KOPS_STATE_STORE=<your-gcp-state-store>
$ PROJECT=`gcloud config get-value project`
$ kops create -f part3.yaml
```
You are now ready to deploy the cluster by executing:
```sh
$ kops update cluster --name part3.k8s.local --yes --admin
```
Your cluster should need around 5-10 minutes to be deployed. You can validate the cluster with
the command:
```sh
$ kops validate cluster --wait 10m
```

The command will terminate when your cluster is ready to use. Afterwards, you can run:
```sh
$ kubectl get nodes -o wide
```

to get the status and details of your nodes as follows:
```
NAME STATUS ROLES AGE VERSION INTERNAL-IP EXTERNAL-IP Oclient-agent-a-s8mr Ready node 5m5s v1.31.5 10.0.16.3 34.79.156.52 Uclient-agent-b-7g2h Ready node 5m10s v1.31.5 10.0.16.7 34.79.109.216 Uclient-measure-m4cg Ready node 4m42s v1.31.5 10.0.16.8 34.22.137.71 Umaster-europe-west1-b-sd4j Ready control-plane 7m45s v1.31.5 10.0.16.6 35.195.216.176 Unode-a-8core-sjn0 Ready node 4m48s v1.31.5 10.0.16.4 35.233.71.64 Unode-b-4core-678h Ready node 5m27s v1.31.5 10.0.16.5 34.38.138.2 U
```
To connect to any of the machines you can run:
```sh
$ gcloud compute ssh --ssh-key-file ~/.ssh/cloud-computing ubuntu@<MACHINE_NAME> \
--zone europe-west1-b
```

Modify the memcached and batch applications YAML files from Parts 1 and 2 of the project and
use the kubectl create commands to launch the workloads in the cluster. You may want to write
automated scripts to launch the jobs. Automated scripts are not a requirement in this part of the
project, but we encourage you to use them here as they will be compulsory in Part 4. The memcached job must start first and continue running throughout the whole experiment, while receiving
a constant load of 30K QPS from the mcperf client. After making sure you have started memcached
and the client load, you can start the batch jobs in the desired order. Your goal is to minimize the
time from the moment the first batch job was started, to the moment the last batch job completes,
while also ensuring that the 95th percentile latency for memcached remains below 1ms.

For Part 3 and Part 4, you must use a modified version of mcperf. It provides two features: it adds
two columns that contain the start and end time for each measurement, and it allows variable traces
(needed for Part 4 of the project). To install the augmented version of mcperf on client-agent-*
and client-measure, follow the instructions below:
```sh
$ sudo sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources
$ sudo apt-get update
$ sudo apt-get install libevent-dev libzmq3-dev git make g++ --yes
$ sudo apt-get build-dep memcached --yes
$ git clone https://github.com/eth-easl/memcache-perf-dynamic.git
$ cd memcache-perf-dynamic
$ make
```

Instead of sweeping the request throughput, as in Part 1, you now want to generate load at a constant
rate of approximately 30K QPS, while periodically reporting latency (e.g. every 10 seconds). To
do this, run the following command on the client-agent-a machine:
```sh
$ ./mcperf -T 2 -A
```

and the following command on the client-agent-b machine:
```sh
$ ./mcperf -T 4 -A
```

and the following command on the client-measure VM:
```sh
$ ./mcperf -s MEMCACHED_IP --loadonly
$ ./mcperf -s MEMCACHED_IP -a INTERNAL_AGENT_A_IP -a INTERNAL_AGENT_B_IP \
--noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 10 \
--scan 30000:30500:5
```

You can get the execution time of each batch job by parsing the JSON output of the kubectl
command that returns information about the jobs, including their start and completion time. To
do this, run the following command after all jobs have been completed:
```sh
$ kubectl get pods -o json > results.json
$ python3 get_time.py results.json
```
where get_time.py is a python script that you can find here.

IMPORTANT: you must delete your cluster when you are not using it! Otherwise,
you will easily use up all of your cloud credits! When you are ready to work on the project
again, you can easily re-launch the cluster with the instructions from above.
To delete your cluster, use the command:
```sh
$ kops delete cluster --name part3.k8s.local --yes
```

## OpenEvolve

For the second subtask of part 3, you will use LLMs to autonomously discover a new scheduling
policy, and you will compare it with your hand-crafted one. To do this, you will utilize the opensource framework OpenEvolve.
OpenEvolve uses LLMs to progressively modify a code snippet to maximize a user-defined score.
For the project, you will be using OpenEvolve to evolve a starting scheduling policy, aiming to
minimize the total makespan while maintaining the SLO goal, similarly to subtask 1.
There are three major components in an OpenEvolve project:

- Initial program: This is the baseline program that the framework will "evolve". The
source file must contain a single block delimited by comments \# EVOLVE-BLOCK-START and
\# EVOLVE-BLOCK-END. The LLM is instructed to modify code only inside this specific block.
Refer to the examples in the OpenEvolve repository for more information.
- Evaluator: The evaluator measures how well the currently evolved program performs. Your
evaluator program will need to run the evolved scheduler, collect metrics to compute a "combined score", which then guides the direction of the next evolution. It’s important to handle
errors gracefully to explicitly inform the LLM that their generated code is incorrect.
- Config: The configuration file config.yaml contains your evolution settings. Here, you will
define the API access to the LLM and, most importantly, the system message. The latter
is extremely important, as it provides the LLM with all the required information to make
sensible decisions. Take time and care in optimizing and perfecting this prompt.
You must set checkpoint_interval: 1 in your config; you can find a compliant template
configuration file in the Git repository.

In order to use OpenEvolve, you will need API access to an LLM. We have granted you access
to the Swiss AI Research Platform, which hosts a selection of different models.
1. Log into the Research Platform with your ETH account to receive your API key
2. Export this key as the environment variable OPENAI_API_KEY
3. Set the api_base field in your config.yaml to https://api.swissai.cscs.ch/v1
4. Set the primary_model field in your config to any available model.

You can install OpenEvolve using pip (or pipx):
```sh
$ pip install openevolve
```

This will make the command openevolve-run available. You can now start the evolution by
running:

```sh
$ openevolve-run --config config.yaml -o <out dir> <initial program> <evaluator>
```

In the output directory, OpenEvolve will create log files and checkpoints, saving the current result
of the evolution. We strongly suggest that you use a different output directory every time you
start a new evolution to make it easier to collect the artifacts for submission. If you run multiple
evolutions with the same output folder, OpenEvolve will start overwriting past checkpoints, which
could potentially result in loss of data required for submission.
Evolution will run for the number of iterations specified in the config; at any time, you can
(gracefully!) stop evolution with Ctrl+C. If you wish, you can resume evolution from a specific
checkpoint by using the --checkpoint argument of openevolve-run.

For a given checkpoint, the best evolved program is saved as
<out_dir>/checkpoints/checkpoint_XXX/best_program.py; at the end of the evolution process,
the best program is also stored in <out_dir>/best. You can then use it to run the same benchmarks
you ran in subtask 1 and add the results to your report. Make sure to note the run log and final
checkpoint directory you are considering when benchmarking, as you are required to submit them
along with your code; see the submission section (3.4) for more information.
We invite you to check out the examples folder in the OpenEvolve repository to get used to the
framework and its different features.
To better track the progress, you can run the OpenEvolve visualizer, which interactively shows the
evolution, along with the metrics from each program and the changes that the LLM applies. To use
it, you can run the following:

```sh
$ git clone https://github.com/algorithmicsuperintelligence/openevolve.git
$ cd openevolve
$ python3 -m venv .venv
$ source .venv/bin/activate
$ pip install -r scripts/requirements.txt
$ python3 scripts/visualizer.py --path <out-directory-or-specific-checkpoint>
```

If given the output directory, the visualizer will show the latest saved checkpoint in real-time.
IMPORTANT: you must delete your cluster when you are not using it! Otherwise,
you will easily use up all of your cloud credits! When you are ready to work on the project
again, you can easily re-launch the cluster with the instructions from above.
To delete your cluster, use the command:

```sh
$ kops delete cluster --name part3.k8s.local --yes
```

### 3.3 Questions

Use the report template to answer the questions and submit your results for Part 3 of the project.

### 3.4 Submission

For Part 3 of the project, we expect you to submit:
• The PDF file containing the answers to the posed questions, in the form of the filled project
report template.
• All YAML files you have modified or newly created.
• All scripts you have used for automation (if you used any).
• All other scripts or files you used, and consider useful for the understanding of your scheduling
policy.
• In the root of your submission archive, place a directory part_3_openevolve/. Inside, place
your config, initial program, evaluator program, and best evolved program. Also, place the log
of the run that generated your best program and the latest checkpoint containing your best
program. These can be found respectively in <out-dir>/logs and <out-dir>/checkpoints;
for convenience, we provide a script openevolve_collect.py that can collect them automatically. Make sure to double-check that all these files correspond to the evolution run that
produced the benchmarked scheduler.
• Your measurement output files, in the format explained below:
– Your submission must contain the measurements for the results described in your report.
– In the root of your submission archive, place two directories called
part_3_1_results_group_XXX and part_3_2_results_group_XXX, where XXX is your
group number represented with 3 digits (e.g. for group 1, XXX equals 001).
– The folder part_3_1_results_group_XXX must contain the results of task 1 (handcrafted policy evaluation), while part_3_2_results_group_XXX must contain the results
of task 2 (OpenEvolve-generated policy evaluation).
– In each directory, place 6 files - 3 .json and 3 .txt files. The .json files must be
named pods_1.json, pods_2.json and pods_3.json. The .txt files must be named
mcperf_1.txt, mcperf_2.txt and mcperf_3.txt.
– Each .json file should contain the full output of the get pods command of the corresponding run.
17
– Each .txt file should contain the output of the mcperf execution for the corresponding
run. You can find an example of the expected mcperf output format here. In the general
case, copying from the console should be sufficient to match the required format. But,
it is your responsibility to make sure that the format of all your .txt files matches the
one in the example given above.
Note: Trailing new lines and whitespaces are ignored. You can use either Unix-like line
endings (\n) or Windows-like line endings (\r\n).
– Please follow the instructions stated above. Divergence from the required format
can lead to subtraction of points.
There are no additional requirements regarding the structure of the other requested files.


# Part 4

In Part 4 of the project you will co-schedule the batch applications on a single 4-core server running
memcached. In contrast to Part 3, the load on the long-running memcached service will now be
dynamically varied, such that the number of cores needed by the memcached service to meet the tail
latency SLO may require more than a single node. Your goal is to design a scheduling policy that
grows and shrinks the resource allocation of memcached and opportunistically uses (temporarily)
available cores to complete the batch jobs as quickly as possible. Your scheduling policy must guarantee a memcached tail latency SLO of 0.8ms 95th percentile latency. For this part of the project,
you will be using a cluster consisting of 4 nodes: a 2-core VM cluster master, a 4-core high memory
VM for the memcached server and batch jobs, a 8-core VM for the mcperf agent, and a 2-core VM
for the mcperf measurement machine.

You are required to implement your own controller to launch jobs and dynamically adjust their
available resources based on your scheduling policy. In this part of the project, we will not be using
Kubernetes because it does not provide an API to change a container’s resource allocation during
runtime. Instead, you will use Docker to launch containers and run the batch workloads, and to
dynamically adjust their resources. For memcached, we provide instructions for installing and running it directly on the VM (rather than in a Docker container) and for using the taskset command
to dynamically adjust its resources. The reason why we do not use Docker to run memcached in
this part of the assignment is that we have observed that memcached’s resources are not effectively
constrained with docker --cpuset-cpus. This occurs due to the fact that most of the processing in
memcached is network packet processing, which executes in kernel threads. Your controller should
monitor CPU utilization and/or other types of resources and metrics to decide if resources need to
be adjusted to meet the SLO. Your controller should make dynamic resource allocation decisions,
such that the batch jobs are completed as quickly as possible, while still enforcing memcached’s SLO.

For this part of the project you should also use the augmented version of mcperf, which is capable
of generating random loads on the memcached server, as well as specific load traces. Refer to the
instructions provided in Part 3 to install this version.

### Implementing the controller and the scheduling policy

We recommend implementing your controller in python and using the Docker Python SDK to manage containers. Alternatively, you may implement the controller in Go using the Docker Go SDK.
You can find examples of managing containers using the Docker SDK, for both Python and Go. If
you plan on using such an SDK, you might find it useful to use the shell command sudo usermod
-a -G docker <your-username>. This will allow you to use the SDK programmatically, without
encountering permission errors. You will also be able to run docker commands without using sudo.

In addition to running containers, you will also need to update containers while they are running.
Updating a container refers to dynamically adjusting the properties of the container, such as the
CPU allocation. You can read more about updating containers in the Docker update command
documentation. You can update docker containers using Docker SDK commands. In case you find
it helpful, you can also pause and unpause containers. This is an option you may explore, but it
is not required. Pausing a container has the effect of temporarily stopping the execution of the
processes in the container (i.e. releasing CPU resources), while retaining the container’s state (i.e.
keeping the container’s memory resources). Unpausing a container resumes the execution of the processes in that container.

Your controller should run on the 4-core high memory memcached server and monitor the CPU utilization. The controller should then use the CPU utilization statistics to make dynamic scheduling
decisions. You can monitor CPU utilization on the server by reading and post-processing data from
/proc/stat files on the VM. There are also language specific options for monitoring metrics, such
as psutil for Python.

In addition to CPU utilization, you can also use other inputs for your scheduling policy if you wish
to do so. This is not required, but may let you implement an even better scheduling policy. Make
sure that your project report contains explanations of any additional controller inputs you choose
to consider in your scheduling policy.

### Evaluating the scheduling policy

You will evaluate your scheduling policy with a dynamic mcperf load trace we provide (see instructions below). You should use mcperf to investigate the performance of your scheduling policy with
various load traces (e.g. try different random seeds and time intervals). Experimenting with various
load traces will allow you to analyze when and why does your policy perform well and to understand
in which scenarios the policy does not adapt appropriately

### Generating the plots

In this part of the project you will be asked to generate some plots which often require you to
aggregate data gathered from different VMs. This can be challenging, since you’ll need to temporally
correlate data across different VMs. A straightforward way to do this is to save the Unix time
whenever you log an event, as this time is roughly synchronized across VMs. You can further use
other information such as dynamic mcperf’s --qps_interval or -t parameter (see documentation
here). Our dynamic mcperf version should also print the simulation’s start and end Unix times in
the output logs by default. Another alternative is to use the shell command date +%s. These times
can then be used when generating the plots to synchronize events that take place on different VMs

## Setup

### Installation

Run the following command to create a kubernetes cluster with 1 master and 3 nodes.

```sh
$ export KOPS_STATE_STORE=<your-gcp-state-store>
$ PROJECT='gcloud config get-value project'
$ kops create -f part4.yaml
You are now ready to deploy the cluster by running:
$ kops update cluster --name part4.k8s.local --yes --admin
```

Your cluster should need around 5-10 minutes to be deployed. You can validate the cluster with
the command:
```sh
$ kops validate cluster --wait 10m
```

The command will terminate when your cluster is ready to use. Afterwards you can run:
```sh
$ kubectl get nodes -o wide
```
to get the status and details of your nodes as follows:

```
NAME STATUS ROLES AGE VERSION INTERNAL-IP EXTERNAL-IP
client-agent-20lc Ready node 4m53s v1.31.5 10.0.16.3 34.76.26.190
client-measure-4lkz Ready node 5m12s v1.31.5 10.0.16.6 34.79.109.216
master-europe-west1-b-th6m Ready control-plane 8m49s v1.31.5 10.0.16.5 34.22.137.71
memcache-server-9806 Ready node 5m16s v1.31.5 10.0.16.4 34.38.138.2
```

You will first need to manually install memcached on the memcache-sever VM. To do so, you must
first use the following commands:

```sh
$ sudo apt update
$ sudo apt install -y memcached libmemcached-tools
```

To make sure the installation succeeded, run the following command:

```sh
$ sudo systemctl status memcached
```

You should see an output similar to the one pasted underneath:
```
memcached.service - memcached daemon
Loaded: loaded (/lib/systemd/system/memcached.service; enabled; vendor preset: enabled)
Active: active (running) since Thu 2021-04-01 08:21:26 UTC; 10min ago
Docs: man:memcached(1)
Main PID: 11796 (memcached)
Tasks: 10 (limit: 4915)
CGroup: /system.slice/memcached.service
-11796 /usr/bin/memcached -m 64 -p 11211 -u memcache -l 127.0.0.1 ...
```

You will need to expose the service to the outside world, and increase its default starting memory.
To do so, open memcached’s configuration file using the command:

```sh
$ sudo vim /etc/memcached.conf
```

To update memcached’s memory limit, look for the line starting with -m and update the value to
1024. Similarly, to expose the memcached server to external requests, locate the line starting with
-l and replace the localhost address with the internal IP of the memcache-server VM. In this
file you can also specify the number of memcached threads by introducing a line starting with -t,
followed by the number of threads. After entering all of the desired changes, save the file, and then
execute the next command to restart memcached with the new configuration:

```sh
$ sudo systemctl restart memcached
```

Running sudo systemctl status memcached again should yield an output similar as before, but
you should see the updated parameters in the command line. If you completed these steps successfully, memcached should be running and listening for requests on the VMs internal IP on port 11211.

On client-agent and client-measure machines, install the augmented version of mcperf following the instructions from Part 3.

On the client-agent VM, you should then run the following command to launch the mcperf
memcached client load agent with 8 threads:

```sh
$ ./mcperf -T 8 -A
```

On the client-measure VM, run the following commands to first load the memcached database
with key-value pairs and then to query memcached with a dynamic load generator, which will
produce a random throughput between 5k and 110k queries per second during each interval. The
throughput target will change and will be assigned to another QPS value for the next time interval.
Note that, in contrast to the previous task, the output appears only at the end of the measurement.
In the example below the interval duration is set to 15 seconds, whilst the overall execution time is
1800 seconds or 30 minutes, this will result in 120 different QPS intervals:

```sh
$ ./mcperf -s INTERNAL_MEMCACHED_IP --loadonly
$ ./mcperf -s INTERNAL_MEMCACHED_IP -a INTERNAL_AGENT_IP \
--noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 1800 \
--qps_interval 15 --qps_min 5000 --qps_max 110000
```

The INTERNAL_MEMCACHED_IP and INTERNAL_AGENT_IP are the internal IPs of the memcache-sever
and client-agent retrieved from the output of kubectl get nodes -o wide.

For more information on the dynamic load generator, and the available options it provides, check
the guide in the README.md of the public repository.
Batch jobs can be started using Docker. For instance, one can start the blackscholes job on
core 0 (--cpuset-cpus="0" parameter) and with 2 threads (-n 2 parameter) using the following
command:

```sh
docker run --cpuset-cpus="0" -d --rm --name parsec \
anakli/cca:parsec_blackscholes \
./run -a run -S parsec -p blackscholes -i native -n 2
```

Feel free to inspect the YAML files for the batch jobs, provided in the previous parts of the project, to
further understand their command line arguments. You can find the rest of the docker images here.
Make sure to use the native datasets for the jobs and the following image versions:
- barnes: anakli/cca:splash2x_barnes
- blackscholes: anakli/cca:parsec_blackscholes
- canneal: anakli/cca:parsec_canneal
- freqmine: anakli/cca:parsec_freqmine
- radix: anakli/cca:splash2x_radix
- streamcluster: anakli/cca:parsec_streamcluster
- vips: anakli/cca:parsec_vips

IMPORTANT: You must delete your cluster when you are not using it! Otherwise,
you will easily use up all of your cloud credits! When you are ready to work on the project
again, you can easily re-launch the cluster using the instructions above.
To delete your cluster, use the following command:
```sh
$ kops delete cluster --name part4.k8s.local --yes
```

### Setting resource limits

taskset is an essential command used for setting the process CPU affinity. For instance, running
taskset -a -cp 0-2 <pid> will bind all threads (-a switch) of the running process indicated by
<pid> (-p parameter) to the CPUs 0, 1 and 2 (-c parameter). One can also use this command
when starting up processes. More information on taskset can be obtained here.

For Docker, the --cpuset-cpus parameter is used to set the cores a container is able to use. This
parameter can be set when spinning up a container (e.g. sudo docker run --cpuset-cpus="0-2"
...) or updated when a container is already running (e.g. docker container update --cpuset-cpus="0-2"
CONTAINER).

You are also free to use other methods to dynamically adjust resource allocation for your jobs. This
can refer to resources other than CPU cores.

### Questions
Use the report template to answer the questions and submit your results for Part 4 of the project.
### Submission
For part 4 of the project, we expect you to submit:
• The PDF file containing the answers to the posed questions, in the form of the filled project
report template.
• The script you used to automate the scheduler.
• All other scripts or files you used, and consider needed/useful for the script above.
• Your measurement output files, in the format explained below:
– Your submission must contain the measurements for the results described in your report.
– In the root of your submission archive, place two directories called part_4_3_results_group_XXX
and part_4_4_results_group_XXX, where XXX is your group number represented with
3 digits (e.g. for group 1, XXX equals 001).
– Each of the directories should have 6 files inside. They must be named jobs_1.txt,
jobs_2.txt, jobs_3.txt and mcperf_1.txt, mcperf_2.txt, mcperf_3.txt.
– Each mcperf_i.txt file should contain the output of the mcperf execution for the corresponding run. You can find an example of the expected mcperf output format here. In
the general case, copying from the console should be sufficient to match the required format. But, it is your responsibility to make sure that the format of all your mcperf_i.txt
files matches the one in the example given above.
Note: Trailing new lines and whitespaces are ignored. You can use either Unix-like line
endings (\n) or Windows-like line endings (\r\n).
– The jobs_i.txt files should contain the container execution log for the corresponding
run.
∗ Since you are not expected to use Kubernetes for this part, you have to produce a
text-based log.
∗ We provide a utility class in Python that does exactly that. Feel free to re-implement
this class in any language you decide to use, but the output must adhere to the
format of the provided Python class.
∗ Each line in the file represents an event. It starts with a date in the ISO format (e.g. 2023-04-12T09:52:37.019688), followed by the event name (start,
end, update_cores, pause, unpause, or custom), and the job name (memcached,
blackscholes, canneal, dedup, ferret, freqmine, radix, vips, scheduler).
∗ A start event must be followed by two more elements that represent: 1) the list of
CPU cores ([0, 1, 2, 3]) the process was assigned at the beginning and 2) the
number of (software) threads it is started with.
∗ An update_cores event has an additional argument that represents the new list of
assigned cores.
∗ A custom event has an arbitrary string (that is URL-encoded) as the last parameter.
Use this event if you are applying different techniques, that are not supported by
the logger, or if you want to add comments to the trace.
∗ Trailing whitespaces and newlines are ignored, you can use either Unix-like line
endings (\n) or Windows-like line endings (\r\n).
∗ The file must start with a start event for the scheduler, and end with an end event
for the scheduler. These two events should not have a core assignment specified.
∗ Remember that each PARSEC job that you start must eventually end.
∗ Remember that memcached needs a start event, but it doesn’t necessarily need an
end. If memcached is already running, log the start memcached event just after the
start scheduler event.
∗ Refer to this file for an example.
– Please follow the instructions stated above. Divergence from the required format
can lead to subtraction of points.
– Please make sure your files are complete and that the measurement files match the plots
and descriptions used in your project report. Divergence from these instructions
can lead to subtraction of points.
There are no additional requirements regarding the structure of the other requested files.