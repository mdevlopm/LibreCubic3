echo 0 > /sys/class/gpio/gpio41/value
sleep 1
echo 1 > /sys/class/gpio/gpio41/value
ulimit -c 0
echo "/dev/null" > /proc/sys/kernel/core_pattern
ps | grep wpa_supplicant | grep -v grep | awk '{print $1}' | xargs kill
ps | grep adbd | grep -v grep | awk '{print $1}' | xargs kill
killall adbd
export USE_MUTABLE_CONFIG=1
export LD_LIBRARY_PATH=/userdata/app/gk:$LD_LIBRARY_PATH
#echo $LD_LIBRARY_PATH

killall gklib
killall gkapi
killall K3SysUi
killall gkcam
./gklib -a /tmp/unix_uds1 printer.cfg &
sleep 5
./gkapi &
./gkcam &
./K3SysUi &
