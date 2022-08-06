"""
@author: Georgi Talmazov

"""


# REFERENCES:
# https://pymotw.com/2/asynchat/
# tuple unpacking https://stackoverflow.com/questions/1993727/expanding-tuples-into-arguments
# https://pymotw.com/2/asyncore/

import asyncore
import queue
import logging, os
import socket
import threading #multiprocessing does not work well in blender
import zlib
import time
from datetime import datetime

import requests
import subprocess
import json
import re
import xml.etree.ElementTree as ET
import platform
import tempfile
import zipfile
class requests_api():
    def __init__(self, addon_v):
        self.api = "https://design.d3tool.com/api.php"
        self.addon_v = addon_v
        login_data = {
            "app_token":"2fd542dbdc745ecd0225d463942de087"
        }
        self.requests_session = requests.Session()
        self.requests_session.post(self.api[:-7] + "auth-login.php", data=login_data)
        #print(self.requests_session)

    def update_addon(self):
        data = {
        'action':"openPlan-update"
        }
        response = json.loads(self.requests_session.post(self.api, data=data).text)
        #print(response)
        if eval(response['version']) == self.addon_v:
            #print("UP TO DATE")
            pass
        elif eval(response['version']) != self.addon_v:
            #print("UPDATE REQUIRED")
            #print(response['update_url'])
            addon_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            #print(addon_dir)
            file = self.requests_session.get(response['update_url'], allow_redirects=True)
            open(os.path.join(tempfile.gettempdir(), "openPlan.zip"), 'wb').write(file.content)
            zip_file_object = zipfile.ZipFile(os.path.join(tempfile.gettempdir(), "openPlan.zip"), 'r')
            zip_file_object.extractall(addon_dir)

    def new_case(self):
        UID , sys_info = self.machine_info()
        data = {
        'machine_id':UID,
        'ip_addr':requests.get('https://checkip.amazonaws.com').text.strip(),
        'errors': 'None',
        'version':str(self.addon_v),
        'system_info':sys_info
        }
        self.requests_session.post(self.api, data=data)
        try: self.update_addon()
        except: pass

    def new_error(self, error):
        UID , sys_info = self.machine_info()
        data = {
        'machine_id':UID,
        'ip_addr':requests.get('https://checkip.amazonaws.com').text.strip(),
        'errors': str(error),
        'version':str(self.addon_v),
        'system_info':sys_info
        }
        self.requests_session.post(self.api, data=data)

    def machine_info(self):
        UID = None
        if platform.system() == "Windows":
            SW_HIDE = 0
            info = subprocess.STARTUPINFO()
            info.dwFlags = subprocess.STARTF_USESHOWWINDOW
            info.wShowWindow = SW_HIDE
            current_machine_id = subprocess.check_output('wmic csproduct get uuid', startupinfo=info).decode().split('\n')[1].strip()
            UID = current_machine_id

            Id = subprocess.check_output(['systeminfo'], startupinfo=info).decode('utf-8').split('\n')
            sys_info = {}
            n=0
            for item in Id:
                item = item.split("\r")[:-1]
                if type(item) == type(list()) and len(item)>0 and "Total Physical Memory" in item[0]:
                    sys_info["physical_memory"] = item[0].strip().replace(" ", "").split(":")[1]
                elif type(item) == type(list()) and len(item)>0 and "OS Name" in item[0]:
                    sys_info["os"] = item[0].strip().replace(" ", "").split(":")[1]
                elif type(item) == type(list()) and len(item)>0 and "Processor(s)" in item[0]:
                    sys_info["cpu"] = Id[n+1].strip().replace(" ", "").split(":")[1]

                n+=1
            return UID, json.dumps(sys_info)

        elif platform.system() == "Darwin": #macOS
            system_profile_data = subprocess.Popen(
                ['system_profiler', '-xml', 'SPHardwareDataType', 'SPSoftwareDataType'], stdout=subprocess.PIPE)
            xml_stdout = ET.fromstring(system_profile_data.stdout.read())
            UID = None
            sys_info = {}
            data = []
            for tag in xml_stdout.iter():
                tag = re.sub(r'\s', '', tag.text)
                if tag != '':
                    data.append(tag)
            #print(data)
            n=0
            for info in data:
                if UID is None and 'serial_number' in info:
                    UID = data[n+1]
                if not 'cpu_type' in sys_info and 'cpu_type' in info:
                    sys_info['cpu_type'] = data[n+1]
                if not 'current_processor_speed' in sys_info and 'current_processor_speed' in info:
                    sys_info['current_processor_speed'] = data[n+1]
                if not 'physical_memory' in sys_info and 'physical_memory' in info:
                    sys_info['physical_memory'] = data[n+1]
                if not 'os' in sys_info and 'os_version' in info:
                    sys_info['os'] = data[n+1]
                n+=1
            #print(UID, json.dumps(sys_info))
            return UID, json.dumps(sys_info)


class log():
    def __init__(self, app):
        logger = logging.getLogger(app)
        hdlr = logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", app+'_'+datetime.strftime(datetime.today() ,"%d_%m_%Y")+'.log'))
        formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
        hdlr.setFormatter(formatter)
        logger.addHandler(hdlr) 
        logger.setLevel(logging.DEBUG)


packet_terminator = '\nEND_TRANSMISSION\n\n\n'
socket_obj = None
thread = None
address = ('127.0.0.1', 5959)
compression = 8
slicer_sysprocess = None

class SlicerComm():
    # https://github.com/pieper/SlicerWeb/blob/master/WebServer/WebServer.py#L1479 adapted from, using QSocketNotifier class
    # https://stackoverflow.com/questions/55494422/python-qtcpsocket-and-qtcpserver-receiving-message QtTcpSocket example
    # https://python.hotexamples.com/examples/PyQt4.QtNetwork/QTcpSocket/-/python-qtcpsocket-class-examples.html 
    class EchoClient():
        """3D Slicer send and receive/handle data from TCP server via Qt event loop.
        """
        
        def __init__(self, host, port, handle = None, debug = False):
            self.debug = debug
            if self.debug == True: log("SLICER")
            from __main__ import qt
            self.received_data = [] #socket buffer
            self.write_buffer = ""
            self.connected = False
            self.cmd_ops = {"TERM" : [self.handle_close,[]]} #dict stores packet command, corresponding function call, and the number of arguments needed to be passed
            self.socket = qt.QTcpSocket()
            try:
                self.socket.connectToHost(host, port)
            except:
                return
            self.socket.readyRead.connect(self.handle_read)
            self.socket.connected.connect(self.handle_connected)
            self.socket.disconnected.connect(self.handle_close)
            self.data_pkt_buff = ""
            if handle is not None: 
                for CMD, handler in handle:
                    self.cmd_ops[CMD] = handler
            return

        def handle_connected(self):
            self.connected = True

        def handle_close(self):
            self.connected = False
            self.socket.close()
            print("DISCONNECTED")

        def handle_read(self):
            data_buff = ""
            timeout = 10 # seconds
            timeout_start = time.time()
            while time.time() < timeout_start + timeout:
                data_buff = self.socket.read(5242880)
                #print(data)
                '''
                self.received_data.append(data.data().decode())
                data = ''.join(self.received_data)
                if packet_terminator in data:
                    self._process_data()
                    self.received_data = []
                    break
                '''
                self.data_pkt_buff += data_buff.data().decode()
                #print(self.data_pkt_buff)
                if packet_terminator in self.data_pkt_buff:
                    data = self.data_pkt_buff.replace(packet_terminator, '')
                    data = data.split(' net_packet: ')
                    #print(data)
                    #try:
                        #print("\n\nNEW\n " + data[1]
                    if packet_terminator in data[1]: data[1] = data[1].replace(packet_terminator, '')
                    for op in  self.cmd_ops.keys():
                        if op in data[1]: data[1] = data[1].replace(op, '')
                    try:
                        data[1] = eval(str(data[1]))
                    except SyntaxError as e:
                        continue
                    else:
                        data[1] = zlib.decompress(data[1]).decode()
                        if data[0] in self.cmd_ops: self.cmd_ops[data[0]](data[1]) #call stored function, pass stored arguments from tuple
                        elif data[0] in self.cmd_ops and len(data) > 2: self.cmd_ops[data[0]][0](data[1], *self.cmd_ops[data[0]][1]) # call stored function this way if more args exist - not tested
                        else: pass

                        self.data_pkt_buff = ""
                        break

                    #except Exception as e:
                    #    if self.debug == True: logging.getLogger("SLICER").exception("Exception occurred") #dump tracestack

            #print(data)

        '''
        def _process_data(self):
            """We have the full ECHO command"""
            data = ''.join(self.received_data)
            #print(data)
            data = data[:-len(packet_terminator)]
            data = data.split(' net_packet: ')
            self.received_data = [] #empty buffer
            #print(data[1])
            try:
                #print("\n\nNEW\n " + data[1]
                if packet_terminator in data[1]: data[1] = data[1].replace(packet_terminator, '')
                data[1] = eval(str(data[1]))
                data[1] = zlib.decompress(data[1]).decode()
                #print(data[1])
                if data[0] in self.cmd_ops: self.cmd_ops[data[0]](data[1]) #call stored function, pass stored arguments from tuple
                elif data[0] in self.cmd_ops and len(data) > 2: self.cmd_ops[data[0]][0](data[1], *self.cmd_ops[data[0]][1]) # call stored function this way if more args exist - not tested
                else: pass
            except Exception as e:
                if self.debug == True: logging.getLogger("SLICER").exception("Exception occurred") #dump tracestack
            return
        '''

        def send_data(self, cmd, data):
            data = str(zlib.compress(str.encode(data, encoding='UTF-8'), compression))
            self.write_buffer = str.encode(cmd.upper() + " net_packet: " + data + packet_terminator)
            self.socket.write(self.write_buffer)
            self.write_buffer = ""
            print("sending CMD: "+ cmd.upper())

class BlenderComm():

    #blender_main_thread = None

    def start():
        try:
            print("started asyncore.loop")
            asyncore.loop(timeout = 0.1, use_poll = True)
            print("exited asyncore.loop")
        except asyncore.ExitNow as e:
            #print(e)
            pass
        #asyncore.loop()

    def init_thread(server_instance, socket_obj):
        blender_thread = threading.current_thread()
        new_thread = threading.Thread()
        new_thread.run = server_instance
        new_thread.start()
        check_thread = threading.Thread(target=BlenderComm.check_main_thread, args=(blender_thread, new_thread, socket_obj,))
        check_thread.start()
        return new_thread
    
    def stop_thread(my_thread):
        my_thread.join()
        print("joining asyncore loop thread")
        #raise asyncore.ExitNow('Server is quitting!')
        
        
        
    def check_main_thread (main_thread, server_thread, socket_obj):
        while main_thread.is_alive(): # and server_thread.is_alive():
            time.sleep(1) #default 5
            #logging.getLogger("BLENDER").exception("running check main thread")
            #print("running check main thread")
        socket_obj.stop_server(socket_obj)
        BlenderComm.stop_thread(server_thread)
        exit()
            



    class EchoClient(asyncore.dispatcher):
        """Sends messages to the server and receives responses.
        """

        # Artificially reduce buffer sizes to illustrate
        # sending and receiving partial messages.
        #ac_in_buffer_size = 64
        #ac_out_buffer_size = 64
        
        def __init__(self, host, port):
            asyncore.dispatcher.__init__(self)
            self.received_data = [] #socket buffer
            self.connected = False
            self.cmd_ops = {"TERM" : [self.handle_close,[]]} #dict stores packet command, corresponding function call, and the number of arguments needed to be passed
            self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
            self.connect((host, port))

        def handle_connect(self):
            self.connected = True
            print("client connected!")

        def handle_close(self):
            self.connected = False
            self.close()

        def handle_read(self):
            data = self.recv(8192)
            #print("INCOMING RAW DATA:")
            #print(data)
            self.received_data.append(data)
            for i in range(0, len(self.received_data)):
                try: self.received_data[i] = self.received_data[i].decode()
                except: pass
            data = ''.join(self.received_data)
            if packet_terminator in data:
                self._process_data()
                self.received_data = []

        def _process_data(self):
            """We have the full ECHO command"""
            data = ''.join(self.received_data)
            data = data[:-len(packet_terminator)]
            data = data.split(' net_packet: ')
            self.received_data = [] #empty buffer
            data[1] = zlib.decompress(data[1]).decode()
            if data[0] in self.cmd_ops: self.cmd_ops[data[0]](data[1]) #call stored function, pass stored arguments from tuple
            elif data[0] in self.cmd_ops and len(data) > 2: self.cmd_ops[data[0]][0](data[1], *self.cmd_ops[data[0]][1])
            else: pass

        def send_data(self, cmd, data):
            data = zlib.compress(str.encode(data), compression)
            self.send(str.encode(cmd.upper() + " net_packet: " + data + packet_terminator))


    class EchoHandler(asyncore.dispatcher_with_send):

        def init(self, instance, cmd_handle = None):
            self.instance = instance
            self.received_data = [] #socket buffer
            self.write_buffer = ""
            self.connected = False
            self.cmd_ops_client = {"TERM" : [self.handle_close,[]]}
            if cmd_handle is not None: 
                self.cmd_ops_client.update(cmd_handle)

        def handle_connect(self):
            self.connected = True

        def handle_close(self):
            self.connected = False
            self.close()
            for client in self.instance.sock_handler:
                if client == self:
                    del self.instance.sock_handler[self.instance.sock_handler.index(self)]
                    print("client instance deleted")
            print("client disconnected")

        def handle_read(self):
            while True:
                data = self.recv(5242880)
                #print(data)
                #self.logger.debug('handle_read() -> %d bytes', len(data))
                self.received_data.append(data.decode())
                data = ''.join(self.received_data)
                if packet_terminator in data:
                    self._process_data()
                    self.received_data = []
                    break

        def _process_data(self):
            """We have the full ECHO command"""
            data = ''.join(self.received_data)
            #print(data)
            if packet_terminator not in data: return
            data = [pkt for pkt in data.split(packet_terminator) if pkt != packet_terminator][:-1]
            #print(data)
            for raw_data in data:
                cmd, payload = raw_data.split(' net_packet: ')
                try:
                    #print(payload)
                    payload = eval(str(payload))
                    payload = zlib.decompress(payload).decode()
                    print("received CMD: " + cmd)
                    #print(data[1])
                    if cmd in self.cmd_ops_client:
                        #self.cmd_ops_client[data[0]](data[1]) #call stored function, pass stored arguments from tuple
                        self.instance.queue.put([cmd, payload])
                    #elif data[0] in self.cmd_ops_client and len(data) > 2: self.cmd_ops_client[data[0]][0](data[1], *self.cmd_ops_client[data[0]][1])
                    else: pass
                except Exception as e:
                    #print(e)
                    if self.instance.debug == True: logging.getLogger("BLENDER").exception("Exception occurred") #dump tracestack
            self.received_data = [] #empty buffer

        def send_data(self, cmd, data):
            print("sent CMD: " + cmd, data)
            data = str(zlib.compress(str.encode(data, encoding='UTF-8'), compression))
            self.send(str.encode(cmd.upper() + " net_packet: " + data + packet_terminator))

    class EchoServer(asyncore.dispatcher):

        def __init__(self, host, port, cmd_handle = None, config_clients = {}, debug = False):
            self.debug = debug
            if self.debug == True: log("BLENDER")
            asyncore.dispatcher.__init__(self)
            self.instance = self
            self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
            self.set_reuse_addr()
            self.bind((host, port))
            self.listen(5) #max number of connected clients
            self.sock_handler = []
            self.cmd_ops = {}
            if cmd_handle is not None: 
                for CMD, handler in cmd_handle:
                    self.cmd_ops[CMD] = handler
            self.config_clients = config_clients
            self.queue = queue.Queue()

        def handle_accepted(self, sock, addr):
            print('Incoming connection from %s' % repr(addr))
            self.sock_handler.append(BlenderComm.EchoHandler(sock))
            self.sock_handler[-1].init(self.instance, self.cmd_ops)
            self.sock_handler[-1].connected = True
            self.sock_handler[-1].send_data("CONFIG_PARAMS", str(self.config_clients))

        def stop_server(self, socket_obj):
            for connected_client in socket_obj.sock_handler:
                connected_client.handle_close()
            self.close()
            #socket_obj = None
            #raise asyncore.ExitNow('Server is quitting!')
            asyncore.close_all()
            self.close()
            print("server stopped")
        

if __name__ == "__main__":
    #socket_obj = EchoClient(address[0], address[1])
    #init_thread(start)
    socket_obj = BlenderComm.EchoClient(address[0], address[1])
    BlenderComm.init_thread(BlenderComm.start)
    #BlenderComm.start()
    time.sleep(10)
    socket_obj.send_data("TEST", "bogus string from GIL CLIENT")
    time.sleep(10)
    
