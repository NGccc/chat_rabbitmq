#!/usr/bin/env python

import os
import uuid
import json
import re
import logging
import time
import argparse
import struct
import pika

import tornado.ioloop
import tornado.web
from tornado import websocket
from tornado.util import bytes_type
from tornado.iostream import StreamClosedError
from tornado_rabbitmq import PikaClient
import pdb

MAX_ROOMS = 100
MAX_USERS_PER_ROOM = 100
MAX_LEN_ROOMNAME = 20
MAX_LEN_NICKNAME = 20

class RoomHandler(object):

    def __init__(self):
        #room to wsconn dict {'room1':set{cid1,cid2,...cidk},'room2':set{}}
        self.room2cid = {}

        #client_id to client info dict {'cid1':{'room':room1, 'nick':nick1, 'wsconn':wsconn1}.'cid2':{}}
        self.client_info = {}

        #room to other servers cid dict {'room1':set{cid1,cid2...cidk},'room2':set{cid...}}
        self.room2ocid = {}
        
        self.pending_cwsconn = {}

    def add_room(self, room, nick):
        """Add nick to room. Return generated client_id"""
        #if the count of this server's room add the other server's room is greater than MAX_ROOMS
        if room not in self.room2cid and len(self.room2cid) + len(self.room2ocid) >= MAX_ROOMS:
            log.error("MAX_ROOMS_REACHED")
            return -1

        if room in self.room2cid:
            if room in self.room2ocid:
                if len(self.room2cid[room]) + len(self.room2ocid[room]) >= MAX_USERS_PER_ROOM:
                    log.error("MAX_USERS_PER_ROOM_REACHED")
                    return -2
            else:
                if len(self.room2cid[room]) >= MAX_USERS_PER_ROOM:
                    log.error("MAX_USERS_PER_ROOM_REACHED")
                    return -2

        #valid check
        roomvalid = re.match(r'[\w-]+$', room)
        nickvalid = re.match(r'[\w-]+$', nick)
        if roomvalid == None:
            log.error("INVALID_ROOM_NAME - ROOM:%s" % (room,))
            return -3
        if nickvalid == None:
            log.error("INVALID_NICK_NAME - NICK:%s" % (nick,))
            return -4
        
        # generate a client id.
        cid = uuid.uuid4().int  
        
        # it's a new room in this server
        if room not in self.room2cid:  
            self.room2cid[room] = set((cid,))
        else:
            self.room2cid[room].add(cid)
        log.debug("ADD_ROOM - ROOM_NAME:%s" % room)
        # process duplicate nicks
        c = 1
        
        name = nick
        nicks = self.nicks_in_room(room)
        while True:
            if name in nicks:
                name = nick + str(c)
            else:
                break
            c += 1
    
        self.add_pending(cid, room, name)
        return cid

    def add_pending(self, client_id, room, nick):
        log.debug("ADD_PENDING - CLIENT_ID:%s" % client_id)
        # we still don't know the WS connection for this client
        # when we get a ws open message from this client_id's client we fill the field 'wsconn'
        self.pending_cwsconn[client_id] = {'room': room, 'nick': nick}

    def remove_pending(self, client_id):
        if client_id in self.pending_cwsconn:
            log.debug("REMOVE_PENDING - CLIENT_ID:%s" % client_id)
            del(self.pending_cwsconn[client_id])  # no longer pending

    def add_client_wsconn(self, cid, conn):
        """Store the websocket connection corresponding to an existing client."""
        self.client_info[cid] = self.pending_cwsconn[cid]
        self.client_info[cid]['wsconn'] = conn
        room = self.pending_cwsconn[cid]['room']
        nick = self.pending_cwsconn[cid]['nick']
        if len(self.room2cid[room])==0:self.room2cid[room]=set((cid,))
        else:
            self.room2cid[room].add(cid)      
        self.remove_pending(cid)
        # add this conn to the corresponding roomates set
        # send "join" and and "nick_list" messages
        self.send_join_msg(cid)

    def send_msg(self , msg_type , conn, message=''):
        username = conn.room_handler.client_info[conn.client_id].get('nick','')
        room     = conn.room_handler.client_info[conn.client_id].get('room','')
        content  = message
        sent_ts  = "%10.6f" % (time.time(),)
        msgtype  = msg_type
        if msg_type == 'nick_list':
            content = []
            try:
                content = self.nicks_in_room(client_room)
            except:
                pass

        msg_dict = {"msgtype":msg_type, "username":username,"client_id":conn.client_id,
                    "payload":content, "sent_ts":sent_ts, "room":str(room)}
        msg_json = json.dumps(msg_dict)
        channel.basic_publish(exchange='compute',routing_key='',body=msg_json)
        

    def remove_client(self, cid):
        """Remove all client information from the room handler."""
        room = ''
        nick = ''
        try:
            room = self.client_info[cid]['room']
            nick = self.client_info[cid]['nick']
        except:
            log.error('remove_client error, dirty cid or client_info error')
            return 

        if cid in self.client_info:
            del(self.client_info[cid])

        if room in self.room2cid and cid in self.room2cid[room]:
            self.room2cid[room].remove(cid)

        if room in self.room2ocid and cid in self.room2ocid[room]:
            self.room2ocid[room].remove(cid)
        
        if room in self.room2cid and len(self.room2cid[room]) == 0:
            del(self.room2cid[room])

        if room in self.room2ocid and len(self.room2ocid[room]) == 0:
            del(self.room2ocid[room])

    def nicks_in_room(self, room):
        """Return a list with the nicknames of the users currently connected to the specified room."""
        nicks = set()
        if room in self.room2cid: 
            for cid in self.room2cid[room]:
                if cid in self.client_info:
                    nicks.add(self.client_info[cid]['nick'])

        if room in self.room2ocid:
            for ocid in self.room2ocid[room]:
                if ocid in self.client_info:
                    nicks.add(self.client_info[ocid]['nick'])
        return nicks

    def send_join_msg(self, cid):
        nick = self.client_info[cid]['nick']
        room = self.client_info[cid]['room']
        msg = {"msgtype": "join", "username": nick,"payload": "joined the chat room",
               "sent_ts": "%10.6f" % (time.time(),),"room":room, "client_id":cid}
        pmessage = json.dumps(msg)
        channel.basic_publish(exchange='compute',routing_key='',body=pmessage)

    def send_leave_msg(self, cid):
        """Send a message of type 'leave', specifying the nickname that is leaving, to all the specified connections."""
        room = 'Default'
        try:
            nick = self.client_info[cid]['nick']
            room = self.client_info[cid]['room']
            msg = {"msgtype": "leave", "username": nick , "room":room, "client_id":cid,
                   "payload": "left the chat room.", "sent_ts": "%10.6f" % (time.time(),)}
            pmessage = json.dumps(msg)
            channel.basic_publish(exchange='compute',routing_key='',body=pmessage)
        except:
            log.error('client_id leave room error,client_id is not in this room:%r' % room)

    def handle_rabbitmq_message(self, info):
        room     = ''
        cid      = 0
        msg_type = ''
        nick     = ''
        dic_info = {}
        try:
            info = info.decode("utf-8")
            dic_info = json.loads(info)
            room     = dic_info['room']
            msg_type = dic_info['msgtype']
            cid      = dic_info['client_id']
            nick     = dic_info['username']
        except:
            log.error('Dirty message from rabbitmq')

        if msg_type == 'leave':
            self.remove_client(cid)
        elif msg_type == 'join':
            if cid not in self.client_info:
                self.client_info[cid]={"room":room,"nick":nick}
                if room not in self.room2ocid:
                    self.room2ocid[room]=set((cid, ))
                else:
                    self.room2ocid[room].add(cid)
        else:
            #can be extended
            pass

        nick_list = self.nicks_in_room(room)
        dic_info['msgtype']='nick_list'
        dic_info['payload']= list(nick_list)
        list_info = json.dumps(dic_info)
        if room in self.room2cid:
            for cid in self.room2cid[room]:
                if cid in self.client_info:
                    conn = self.client_info[cid]['wsconn']
                    conn.write_message(info)
                    if msg_type == 'leave' or msg_type == 'join':
                        conn.write_message(list_info)

class MainHandler(tornado.web.RequestHandler):

    def initialize(self, room_handler):
        self.room_handler = room_handler

    def get(self, action=None):
        """Render chat.html if required arguments are present, render main.html otherwise."""
        if not action:  # init startup sequence, won't be completed until the websocket connection is established.
            try:
                room = self.get_argument("room")
                nick = self.get_argument("nick")
                # this alreay calls add_pending
                client_id = self.room_handler.add_room(room, nick)
                emsgs = ["The nickname provided was invalid. It can only contain letters, numbers, - and _.\nPlease try again.",
                         "The room name provided was invalid. It can only contain letters, numbers, - and _.\nPlease try again.",
                         "The maximum number of users in this room (%d) has been reached.\n\nPlease try again later." % MAX_USERS_PER_ROOM,
                         "The maximum number of rooms (%d) has been reached.\n\nPlease try again later." % MAX_ROOMS]
                if client_id == -1 or client_id == -2:
                    self.render("templates/maxreached.html",
                                emsg=emsgs[client_id])
                else:
                    if client_id < -2:
                        self.render("templates/main.html",
                                    emsg=emsgs[client_id])
                    else:
                        self.set_cookie("ftc_cid", str(client_id))
                        self.render("templates/chat.html", room_name=room)
            except tornado.web.MissingArgumentError:
                self.render("templates/main.html", emsg="")
        else:
            # drop client from "pending" list. Client cannot establish WS
            # connection.
            if action == "drop":
                client_id = int(self.get_cookie("ftc_cid", 0))
                print("\n\n\n\n\nclient_id=%r\n\n\n\n\n" % client_id)
                if client_id:
                    self.room_handler.remove_pending(client_id)
                    self.render("./templates/nows.html")

class ClientWSConnection(websocket.WebSocketHandler):

    def initialize(self, room_handler):
        """Store a reference to the "external" RoomHandler instance"""
        self.room_handler = room_handler

    def open(self):
        #pdb.set_trace()
        '''receive ws open message from client'''
        self.client_id = int(self.get_cookie("ftc_cid", 0))
        log.debug("OPEN_WS - CLIENT_ID:%d" % self.client_id)
        self.room_handler.add_client_wsconn(self.client_id, self)

    def on_message(self, message):
        import pdb
        msg = json.loads(message)
        message = msg.get('payload','')
        self.room_handler.send_msg('text' , self, message)

    def make_frame(self, message):
        opcode = 0x1
        message = tornado.escape.utf8(message)
        assert isinstance(message, bytes_type)
        finbit = 0x80
        mask_bit = 0
        frame = struct.pack("B", finbit | opcode)
        l = len(message)
        if l < 126:
            frame += struct.pack("B", l | mask_bit)
        elif l <= 0xFFFF:
            frame += struct.pack("!BH", 126 | mask_bit, l)
        else:
            frame += struct.pack("!BQ", 127 | mask_bit, l)
        frame += message
        return frame

    def write_frame(self, frame):
        try:
            self.stream.write(frame)
        except StreamClosedError:
            pass

    def on_close(self):
        #pdb.set_trace()
        log.debug("CLOSE_WS - CLIENT_ID:%s" % str(self.client_id))
        self.room_handler.send_leave_msg(self.client_id)

    def allow_draft76(self):
        return True

def setup_cmd_parser():
    p = argparse.ArgumentParser(
        description='Simple WebSockets-based text chat server.')
    p.add_argument('-i', '--ip', action='store',
                   default='192.168.1.101', help='Server IP address.')
    p.add_argument('-p', '--port', action='store', type=int,
                   default=8888, help='Server Port.')
    p.add_argument('-g', '--log_file', action='store',
                   default='logsimplechat.log', help='Name of log file.')
    p.add_argument('-f', '--file_log_level', const=1, default=0, type=int, nargs="?",
                   help="0 = only warnings, 1 = info, 2 = debug. Default is 0.")
    p.add_argument('-c', '--console_log_level', const=1, default=3, type=int, nargs="?",
                   help="0 = No logging to console, 1 = only warnings, 2 = info, 3 = debug. Default is 0.")
    p.add_argument('-rp', '--rabbitmq_port', action='store', type=int,
                   default=5672, help='rabbitmq Server Port.')
    return p


def setup_logging(args):
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)   # set maximum level for the logger,
    formatter = logging.Formatter('%(asctime)s | %(thread)d | %(message)s')
    loglevels = [0, logging.WARN, logging.INFO, logging.DEBUG]
    fll = args.file_log_level
    cll = args.console_log_level
    fh = logging.FileHandler(args.log_file, mode='a')
    fh.setLevel(loglevels[fll])
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    if cll > 0:
        sh = logging.StreamHandler()
        sh.setLevel(loglevels[cll])
        sh.setFormatter(formatter)
        logger.addHandler(sh)
    return logger

class Application(tornado.web.Application):
    def __init__(self, callback_ins, port):
        self.recieve = PikaClient(callback_ins, port)
        self.recieve.connect()

        handlers = (
            (r"/(|drop)", MainHandler, {'room_handler': room_handler}),
            (r"/ws", ClientWSConnection, {'room_handler': room_handler})
        )
        settings = dict(
            static_path=os.path.join(os.path.dirname(__file__), "static"),
            cookie_secret="",
            xsrf_cookies=False,
            login_url="/",
            debug=True
        )
        
        tornado.web.Application.__init__(self, handlers, **settings)


if __name__ == "__main__":
    '''Basic setting'''
    parse = setup_cmd_parser()
    args  = parse.parse_args()
    rp    = args.rabbitmq_port
    log   = setup_logging(args)
    room_handler = RoomHandler()

    '''
    Initialize tornado and connect with rabbitmq by using pika
    '''
    app=Application(room_handler, rp)
    app.listen(args.port, args.ip)
    
    parameters = pika.URLParameters('amqp://guest:guest@wojiushishen:' + str(rp) +'/%2F')
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.exchange_declare(exchange='compute',type='fanout')

    log.info('START_SERVER - PID:%s' % (os.getpid()))
    log.info('LISTEN - IP:%s - PORT:%s' % (args.ip, args.port))
    tornado.ioloop.IOLoop.instance().start()