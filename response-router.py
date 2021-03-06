import os
import json
import sys
import time
import logging
import threading
from proton import Message
from proton.handlers import MessagingHandler
from proton.reactor import ApplicationEvent, Container, EventInjector
from tornado.web import Application, RequestHandler
from tornado.ioloop import IOLoop

###############################################################################
# ############################### Logging #################################
###############################################################################

formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')


def logger_setup(name, level=os.environ['LOG_LEVEL']):
    """Setup different loggers here"""

    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(sh)
    logger.propagate = False

    return logger


def logger_file_setup(name, file_name, level=os.environ['LOG_LEVEL']):
    """Setup different file based loggers here"""

    file_handler = logging.FileHandler(file_name)
    file_handler.setFormatter(formatter)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(file_handler)

    return logger


general_log = logger_setup(os.environ['LOGGER_NAME'])
time_log = logger_setup(' Timing Response Router 1 ')

# general_log = logger_setup(os.environ['LOGGER_NAME'],
#                            os.environ['LOG_PATH_GENERAL'])
# time_log = logger_setup(' Timing Response Router 1 ','/logs/rr1time.log')

###############################################################################
# ############################### Logging #################################
###############################################################################


class Publisher(MessagingHandler):
    def __init__(self, server):
        super(Publisher, self).__init__()
        self.server = server
        self.json_to_parse = {}
        self.send_topic = []
        self.sender = None
        self.sender_buffer = []
        self.car_to_send = ""
        self.rr_time_start = 0
        self.user = os.environ['MSG_BROKER_USER']
        self.password = os.environ['MSG_BROKER_PASSWORD']
        self.connection = None
        self.timeout_limit_max = 64
        self.timeout_limit_min = 1
        self.timeout_limit = 1

    def on_start(self, event):
        conn = event.container.connect(self.server, user=self.user,
                                       password=self.password)
        for topic in self.send_topic:
            self.sender = event.container.create_sender(conn,
                                                        'topic://%s' % topic)
        self.connection = conn

    def on_disconnected(self, event):
        """
        Called when the connection between the client and the broker is
        disconnected
        """

        general_log.error("The connection to broker is lost." +
                          "Trying to reestablish the connection")
        self.connection.close()

        if self.timeout_limit < self.timeout_limit_max:
            time.sleep(self.timeout_limit)
            conn = event.container.connect(self.server, user=self.user,
                                           password=self.password)
            general_log.error("waited for "+str(self.timeout_limit) +
                              " seconds\n")
            for topic in self.send_topic:
                self.sender = event.container.create_sender(
                    conn,
                    'topic://%s' % topic)
            self.connection = conn
            self.timeout_limit *= 2
            state = str(self.get_connection_state())
            general_log.error(state + " connection state\n")
            if state == 36:
                execute_order_36()

        else:
            time.sleep(self.timeout_limit)
            conn = event.container.connect(self.server, user=self.user,
                                           password=self.password)
            general_log.error("waited for " +
                              str(self.timeout_limit)+" seconds\n")
            for topic in self.send_topic:
                self.sender = event.container.create_sender(
                    conn, 'topic://%s' % topic)
            self.connection = conn
            state = str(self.get_connection_state())
            general_log.error(state + " connection state\n")
            if state == 36:
                execute_order_36()

        return super().on_disconnected(event)

    def get_connection_state(self):
        try:
            state = self.connection.state
            if state == 18:
                self.timeout_limit = self.timeout_limit_min

        except Exception:
            general_log.error("Cannot get connection state")
            return 0
        return state

    def on_my_custom_send(self, event):
        """ Function to send messages to the car client through AMQP broker """

        if self.sender_buffer and self.sender.credit:
            car_id_send = self.sender_buffer.pop(0)
            message_body = self.sender_buffer.pop(0)
            general_log.debug('sending something... %s' % message_body)
            general_log.debug('CAR ID... %s' % car_id_send)
            message = Message(
                body=message_body,
                # , 'ref_timestamp_fc':self.json_to_parse["ref_timestamp_fc"]})
                properties={'Car_ID': car_id_send})
            message.durable = False
            self.sender.send(message)
            # general_log.info(
            #     "In Response router it takes " +
            #     str((time.time()-self.rr_time_start)*1000)+" ms to send")

    def on_sendable(self, event):
        """
        called after the sender is created only as a sender credit is made
        """

        self.on_my_custom_send(event)

    def details(self):
        """
        For every message received from the CLM convert the message received
        into station id and payload"""

        payload_details = []
        if self.json_to_parse != {} and "message" in self.json_to_parse:
            dummy_msg = self.json_to_parse["message"]
            stations_id = self.json_to_parse["Car_ID"]
            msg_payload = dummy_msg
            payload_details.append(stations_id)
            payload_details.append(msg_payload)
            return payload_details
        elif self.json_to_parse != {} and "EP" in self.json_to_parse:
            dummy_msg = self.json_to_parse["EP"]
            stations_id = self.json_to_parse["Car_ID"]
            msg_payload = dummy_msg
            payload_details.append(stations_id)
            payload_details.append(msg_payload)
            return payload_details
        else:
            return payload_details


###############################################################################
# Handles calls from the Maneuvering Service #################################
###############################################################################

class MS_ApiServer(RequestHandler):
    def post(self, id):
        """
        Handles the behaviour of POST calls from the maneuvering service
        suggestion to car
        """
        # self.write(json.loads(self.request.body))
        rr_time_start = time.time()
        json_form = json.loads(self.request.body)

        try:
            for ind_msg in json_form["messages"]:
                client_pub.json_to_parse = ind_msg
                client_pub.car_to_send = ind_msg["Car_ID"]
                client_pub.sender_buffer.append(client_pub.details()[0])
                client_pub.sender_buffer.append(client_pub.details()[1])
                events.trigger(ApplicationEvent("my_custom_send"))
            json_form["rr_process_time"] = (time.time()-rr_time_start)*1000
            json_form["broker_conn_state"] =\
                str(client_pub.get_connection_state())
        except Exception as e:
            general_log.error(
                str(e) +
                ": Error managing msg - probably client_pub not initialised.")
            json_form["broker_conn_state"] = "-1"
        self.write(json_form)
        # client_pub.json_to_parse = json_form
        # client_pub.car_to_send = client_pub.json_to_parse["Car_ID"]
        # client_pub.sender_buffer.append(client_pub.details()[1])
        # events.trigger(ApplicationEvent("my_custom_send"))

    def put(self, id):
        """Handles the behaviour of PUT calls"""
        pass

    def get(self, id):
        """ Get connection state with broker"""
        self.write(
            {
                "Connection_state": str(client_pub.get_connection_state()),
                "AMQP_broker_endpoint": client_pub.server
            })

    def delete(self, id):
        """Handles the behaviour of DELETE calls"""
        global items
        new_items = [item for item in items if item['id'] is not int(id)]
        items = new_items
        self.write({'message': 'Item with id %s was deleted' % id})


###############################################################################
# Handles calls to change car endpoint ########################################
###############################################################################

class LM_ApiServer(RequestHandler):
    def post(self, id):
        """Handles the behaviour of POST calls from the local manager"""
        json_form = json.loads(self.request.body)
        self.write(json_form)
        rr_time_start = time.time()
        client_pub.json_to_parse = json_form
        client_pub.rr_time_start = rr_time_start
        client_pub.car_to_send = client_pub.json_to_parse["Car_ID"]
        client_pub.sender_buffer.append(client_pub.json_to_parse["EP"])
        events.trigger(ApplicationEvent("my_custom_send"))

    def put(self, id):
        """Handles the behaviour of PUT calls"""
        global items
        new_items = [item for item in items if item['id'] is not int(id)]
        items = new_items
        self.write({'message': 'Item with id %s was updated' % id})

    def delete(self, id):
        """Handles the behaviour of DELETE calls"""
        global items
        new_items = [item for item in items if item['id'] is not int(id)]
        items = new_items
        self.write({'message': 'Item with id %s was deleted' % id})


class RR_TestServer(RequestHandler):
    def get(self, test_id):
        """Handles the behaviour of GET calls from the local manager"""
        if test_id == "ORDER36":
            execute_order_36()
        else:
            general_log.info("test req: "+str(test_id))


def make_app():
    urls = [
        (r"/api/item/from_ms_api/([^/]+)?", MS_ApiServer),
        (r"/api/item/from_local_mgr_api/([^/]+)?", LM_ApiServer),
        (r"/api/item/test/([^/]+)?", RR_TestServer)
    ]
    return Application(urls, debug=True)


##############################################################################
# TREACE THREAD ##############################################################
##############################################################################


class TraceThread(threading.Thread):
    """ Simple thread class to kill threads using traces"""

    def __init__(self, *args, **keywords):
        threading.Thread.__init__(self, *args, **keywords)
        self.killed = False

    def start(self):
        self.__run_backup = self.run
        self.run = self.__run
        threading.Thread.start(self)

    def __run(self):
        sys.settrace(self.globaltrace)
        self.__run_backup()
        self.run = self.__run_backup

    def globaltrace(self, frame, why, arg):
        if why == 'call':
            return self.localtrace
        else:
            return None

    def localtrace(self, frame, why, arg):
        if self.killed:
            if why == 'line':
                raise SystemExit()
        return self.localtrace

    def kill(self):
        self.killed = True

##############################################################################
# KILL OLD THREADS ###########################################################
##############################################################################


def kill_old_threads():
    """ Kill older threads in case of config update"""
    response = True
    for i in threading.enumerate():
        if i is not threading.main_thread() and isinstance(i, TraceThread):
            i.kill()
            response = False
        else:
            pass
    return response

######


def execute_order_36():
    general_log.warning("Execute Order 36!")
    restart_rr()


def restart_rr():

    global client_pub
    global events
    global general_log

    general_log.warning("restart_rr triggered")

    general_log.info("killing all trace treads...")

    while not kill_old_threads():
        pass

    general_log.info("all trace treads killed!")
    client_pub = Publisher(os.environ['MSG_BROKER_ADDR'])
    container = Container(client_pub)
    events = EventInjector()
    container.selectable(events)
    qpid_thread = TraceThread(target=container.run)
    client_pub.send_topic = [os.environ['SEND_TOPIC']]
    qpid_thread.start()

    general_log.info("qpid thread started")


if __name__ == '__main__':

    app = make_app()
    app.listen(os.environ['API_PORT'])
    print("Started Response Router 1 REST Server")
    client_pub = None
    events = None
    # client_pub = Publisher(os.environ['MSG_BROKER_ADDR'])
    # container = Container(client_pub)
    # events = EventInjector()
    # container.selectable(events)
    # qpid_thread = Thread(target=container.run)
    # client_pub.send_topic = [os.environ['SEND_TOPIC']]
    # qpid_thread.start()
    restart_rr()
    IOLoop.instance().start()


"""
Env vars required
os.environ['LOG_LEVEL']
os.environ['LOGGER_NAME'
os.environ['MSG_BROKER_USER']
os.environ['MSG_BROKER_PASSWORD']
os.environ['API_PORT']
os.environ['MSG_BROKER_ADDR']
os.environ['SEND_TOPIC']
"""
