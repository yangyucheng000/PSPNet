#!/bin/bash
#   d
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================

if [ $# != 2 ]
then
    echo "=============================================================================================================="
    echo "Usage: bash scripts/run_distribute_train_ascend.sh [RANK_TABLE_FILE] [YAML_PATH]"
    echo "Please run the script as: "
    echo "bash /PSPNet/scripts/run_distribute_train_ascend.sh [RANK_TABLE_FILE] [YAML_PATH]"
    echo "for example: bash scripts/run_distribute_train_ascend.sh /PSPNet/scripts/config/RANK_TABLE_FILE PSPNet/config/voc2012_pspnet50.yaml"
    echo "=============================================================================================================="
    exit 1
fi

export RANK_SIZE=8
export RANK_TABLE_FILE=$1
export YAML_PATH=$2
export HCCL_CONNECT_TIMEOUT=6000

for((i=0;i<RANK_SIZE;i++))
do
    export DEVICE_ID=$i
    rm -rf LOG$i
    mkdir ./LOG$i
    cp ./*.py ./LOG$i
    cp -r ./src ./LOG$i
    cd ./LOG$i || exit
    export RANK_ID=$i
    echo "start training for rank $i, device $DEVICE_ID"
    env > env.log
    python3 train.py --config="$YAML_PATH"> ./log.txt 2>&1 &

    cd ../
done