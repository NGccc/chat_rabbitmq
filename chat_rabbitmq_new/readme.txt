If you use it in a pc to test whether it run correctly,you should start at least two rabbitmq server through the command below:
sudo RABBITMQ_NODE_PORT=portofNodeA  RABBITMQ_NODENAME=NodeA rabbitmq-server -detached
sudo RABBITMQ_NODE_PORT=portofNodeB  RABBITMQ_NODENAME=NodeB rabbitmq-server -detached

sudo rabbitmqctl -n NodeB stop_app
sudo rabbitmqctl -n NodeB reset
sudo rabbitmqctl -n NodeB join_cluster NodeA@yourhostname
sudo rabbitmqctl -n NodeB start_app

After that, a rabbitmq cluster is built in your pc,and then, you can run two server binding the ports respectively.
for instance,

python server.py -p 8888 -rp portofNodeA
python server.py -p 8889 -rp portofNodeB

You will get two server in a pc,and their rabbitmq server is running in portofNodeA port and portofNodeB port.

Finally, you can open your browser and test it.
