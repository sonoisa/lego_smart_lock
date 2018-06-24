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

# 下記の環境変数が設定されている必要があります。
# SLACK_TOKEN: SlackボットのAPIトークン（例:"xoxb-..."）
# SLACK_BOT_NAME: Slackボットの名前（例:"keybot"）
# SLACK_REMINDER_CHANNEL: 鍵閉め忘れ通知先チャネル名（例:"#alert"）
# API_SECURE_KEY: スマートロックAPIのセキュアキー（例:セキュアなランダムURL文字列）
# API_PORT: スマートロックAPIのポート番号（例:"3000"）

export SLACK_API_URL="https://slack.com/api/chat.postMessage"

rm nohup.out
nohup python /home/pi/legolocker/legolocker.py &
echo $! > $pidfile

echo -e "\nStarted."
