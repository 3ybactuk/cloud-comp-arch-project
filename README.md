# Cloud Computing Architecture Project

This repository contains starter code for the Cloud Computing Architecture course project at ETH Zurich. Students will explore how to schedule latency-sensitive and batch applications in a cloud cluster. Please follow the instructions in the project handout. 







# 1. Переменные окружения (нужны в каждом новом терминале)
export KOPS_STATE_STORE=gs://cca-eth-2026-group-087-anikiforova/
export PROJECT=$(gcloud config get-value project)

# 2. Создать конфигурацию кластера в bucket
kops create -f part3.yaml

# 3. Добавить SSH ключ
kops create secret --name part3.k8s.local sshpublickey admin -i ~/.ssh/cloud-computing.pub

# 4. Задеплоить кластер
kops update cluster --name part3.k8s.local --yes --admin

# 5. Подождать пока кластер поднимется (~5-10 минут)
kops validate cluster --wait 10m
kubectl label node client-agent-a-hh2g cca-project-nodetype=client-agent-a
kubectl label node client-agent-b-885j cca-project-nodetype=client-agent-b
kubectl label node client-measure-9rf5 cca-project-nodetype=client-measure
kubectl label node node-a-8core-489m cca-project-nodetype=node-a-8core
kubectl label node node-b-4core-8nhd cca-project-nodetype=node-b-4core

kubectl get nodes --show-labels | grep cca-project-nodetype




chmod +x setup_mcperf.sh
bash setup_mcperf.sh

Параллельно на трёх нодах занимает ~5-7 минут. Проверить что всё установилось:

for NODE in $(kubectl get nodes -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | grep -E 'client-agent|client-measure'); do
echo -n "$NODE: "
gcloud compute ssh --ssh-key-file ~/.ssh/cloud-computing "ubuntu@$NODE" \
--zone europe-west1-b \
--command 'ls ~/memcache-perf-dynamic/mcperf 2>/dev/null && echo OK || echo MISSING'
done

# Шаг 1: Скрининг всех 5 политик (кластер НЕ удаляем)
python3 run_part3a.py \
--policy-config part3_policies.json \
--output-dir part3-screening

# Посмотреть результаты — какие политики прошли gates
cat part3-screening/screening_summary.json

# Шаг 2: Запустить лучшую политику ещё 2 раза вручную
# (пока кластер ещё жив)
python3 run_part3a.py --policies p02_balanced_waves   # прогон 2
python3 run_part3a.py --policies p02_balanced_waves   # прогон 3

# Шаг 3: Только теперь удалить кластер
kops delete cluster --name part3.k8s.local --yes




Проверки состояния кластера
bash# Кластер жив и все ноды Ready?
kops validate cluster --name part3.k8s.local
kubectl get nodes --show-labels | grep cca-project-nodetype

# Посмотреть все ноды с IP адресами
kubectl get nodes -o wide

# Что сейчас запущено (поды)
kubectl get pods -o wide

# Что сейчас запущено (джобы)
kubectl get jobs

# Залезть на конкретную ноду если нужно
gcloud compute ssh --ssh-key-file ~/.ssh/cloud-computing ubuntu@<NODE-NAME> --zone europe-west1-b

Удалить кластер (обязательно после работы!)
bashkops delete cluster --name part3.k8s.local --yes
Проверить что удалился — через минуту после команды:
bashkops validate cluster --name part3.k8s.local
# должно быть: "cluster not found" или ошибка — это нормально, значит удалён


Через минуту после запуска проверь в отдельном терминале:
# Все три mcperf процесса живы?
ssh -i ~/.ssh/cloud-computing ubuntu@34.77.245.16 'pgrep -a mcperf'
ssh -i ~/.ssh/cloud-computing ubuntu@34.38.186.247 'pgrep -a mcperf'
ssh -i ~/.ssh/cloud-computing ubuntu@35.195.196.187 'pgrep -a mcperf'

# Measure пишет строки?
ssh -i ~/.ssh/cloud-computing ubuntu@35.195.196.187 'wc -l /tmp/mcperf_part3_measure.txt'