pidfile=legolocker.pid
if [ -f $pidfile ]; then
	pid=`cat $pidfile`
	kill -INT $pid >& /dev/null
	sleep 1
	kill $pid >& /dev/null
	if [ $? -ne 0 ]; then
		echo "Operation not permitted."
		return 1
	fi

	echo -n "Stopping..."
	while true
	do
		kill -0 $pid >& /dev/null
		if [ $? -ne 0 ]; then
			break
		fi
		sleep 3
		echo -n "."
	done

	echo -e "\nStopped."
fi
