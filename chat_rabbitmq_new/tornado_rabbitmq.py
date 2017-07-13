#-*- coding:utf-8 -*-
#!/usr/bin/python
import json
import redis
import logging
import uuid
import pdb
try:
    import pika
    from pika.adapters.tornado_connection import TornadoConnection
except ImportError:
    pika = None


try:
    import tornado
    import tornado.ioloop
except ImportError:
    tornado = None

logger = logging.getLogger('main.recieve_tornado')

class PikaClient(object):
    callbacks = {}
    def __init__(self, callback_ins, port):
        self.callback_ins=callback_ins
        self.queue_name = "q%d" % uuid.uuid4().int
        if tornado is None:
            raise Exception('You must add tornado to your requirements!')
        if pika is None:
            raise Exception('You must add pika to your requirements!')

        self.ioloop = tornado.ioloop.IOLoop.instance()
        self.connection = None
        self.channel = None

        self._delivery_tag = 0
        self.parameters = pika.URLParameters("amqp://guest:guest@wojiushishen:" + str(port) + "/%2F")

    def connect(self):
        self.connection = TornadoConnection(self.parameters, on_open_callback=self.on_connected, stop_ioloop_on_close=False)
        self.connection.add_on_close_callback(self.on_closed)


    def on_connected(self, connection):
        logger.info('PikaClient: connected to RabbitMQ')
        self.connection.channel(self.on_exchange_declare)


    def on_exchange_declare(self, channel):
        logger.info('PikaClient: Channel %s open, Declaring exchange' % channel)
        self.channel = channel
        self.channel.exchange_declare(self.on_queue_declare, exchange='compute', type='fanout')
        

    def on_queue_declare(self, method_frame):
        logger.info('PikaClient: Channel open, Declaring queue')
        self.result = self.channel.queue_declare(self.on_queue_bind, queue=self.queue_name, durable=True)


    def on_queue_bind(self, method_frame):
        logger.info('Queue bound')
        self.channel.queue_bind(self.on_consume_bind, queue=self.queue_name, exchange="compute")


    def on_consume_bind(self, frame):
        self.channel.basic_qos(prefetch_count=1)
        self.channel.basic_consume(self.on_response, queue=self.queue_name, no_ack=False)


    def on_response(self, channel, method, properties, body):
        message = body
        self.callback_ins.handle_rabbitmq_message(message)
        channel.basic_ack(delivery_tag = method.delivery_tag)  
        logger.info('Recieve a new Message: %r' % message)

    def on_closed(self, connection):
        logger.info('PikaClient: rabbit connection closed')
        self.connection.close()
        self.channel.close()
        self.ioloop.stop()