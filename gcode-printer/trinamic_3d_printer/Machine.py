from Queue import Queue, Empty
import sys
import logging
import re
from threading import Thread
import serial
import time

__author__ = 'marcus'

_default_serial_port = "/dev/ttyO1"
_default_timeout = 5
_commandEndMatcher = re.compile(";")    #needed to search for command ends
_min_command_buffer = 10 # how much arduino buffer needs to be filled before we start
_max_command_buffer = 3 # how much arduino buffer to preserve
#todo come up with better names!

_logger = logging.getLogger(__name__)


class Machine():
    def __init__(self, serialport=None):
        if serialport is None:
            serialport = _default_serial_port
        self.serialport = serialport
        self.remaining_buffer = ""
        self.machine_connection = None
        self.command_queue = Queue()
        self.batch_mode = False

    def connect(self):
        if not self.machine_connection:
            machineSerial = serial.Serial(self.serialport, 115200, timeout=_default_timeout)
            self.machine_connection = _MachineConnection(machineSerial)
        init_command = MachineCommand()
        init_command.command_number = 9
        reply = self.machine_connection.send_command(init_command)
        if reply.command_number != 0:
            raise MachineCommand("Unable to start")

    def disconnect(self):
        if self.machine_connection:
            self.machine_connection.run_on = False

    def set_current(self, motor=None, current=None):
        command = MachineCommand()
        command.command_number = 1
        command.arguments = (
            int(motor),
            int(current * 1000)
        )
        reply = self.machine_connection.send_command(command)
        if not reply or reply.command_number != 0:
            raise MachineError("Unable to set motor current", reply)

    def move_to(self, motor, target, speed, geared_motors=None):
        command = MachineCommand()
        command.command_number = 10
        command.arguments = [
            int(motor),
            int(target),
            float(speed)
        ]
        if geared_motors:
            for geared_motor in geared_motors:
                command.arguments.append(int(geared_motor['motor']))
                command.arguments.append(float(geared_motor['gearing']))
                #todo this cannot work - we should block until the queue length is big enough
        reply = self.machine_connection.send_command(command)
        if not reply or reply.command_number != 0:
            raise MachineError("Unable to set move motor", reply)
        if self.batch_mode:
            command_buffer_length = int(reply.arguments[0])
            command_max_buffer_length = int(reply.arguments[1])
            command_buffer_free = command_max_buffer_length - command_buffer_length
            command_queue_running = int(reply.arguments[2]) > 0
            if not command_queue_running and command_buffer_length > _min_command_buffer:
                start_command = MachineCommand()
                start_command.command_number = 11
                start_command.arguments = [1]
                reply = self.machine_connection.send_command(start_command)
                #TODO and did that work??
            if command_queue_running and command_buffer_free <= _max_command_buffer:
                buffer_free = False
                while not buffer_free:
                    #sleep a bit
                    time.sleep(0.1)
                    info_command = MachineCommand()
                    info_command.command_number = 31
                    reply = self.machine_connection.send_command(info_command)
                    command_buffer_length = int(reply.arguments[0])
                    command_max_buffer_length = int(reply.arguments[1])
                    command_buffer_free = command_max_buffer_length - command_buffer_length
                    buffer_free = (command_buffer_free > _max_command_buffer)
                    _logger.debug("waiting for free buffer")
        else:
        #while self.machine_connection.internal_queue_length > 0:
            pass # just wait TODO timeout??

    def set_acceleration_settings(self, motor, max_acceleration, max_deceleration=None, start_bow=None, end_bow=None):
        #reconstruct all values
        if not max_deceleration:
            max_deceleration = max_acceleration
        if not start_bow:
            start_bow = max_acceleration / 3 #todo test
        if not end_bow:
            end_bow = start_bow
        command = MachineCommand()
        command.command_number = 3
        command.arguments = [
            int(motor),
            int(max_acceleration),
            int(max_deceleration),
            int(start_bow),
            int(end_bow)
        ]
        reply = self.machine_connection.send_command(command)
        if not reply or reply.command_number != 0:
            raise MachineError("Unable to set acceleration settings")


class _MachineConnection:
    def __init__(self, machine_serial):
        self.listening_thread = Thread(target=self)
        self.machine_serial = machine_serial
        self.remaining_buffer = ""
        self.response_queue = Queue()
        #let's suck empty the serial connection by reading everything with an extremely short timeout
        while machine_serial.inWaiting():
            machine_serial.read()
            #and wait for the next ';'
        init_start = time.clock()
        last = ''
        while not last is ';' and time.clock() - init_start < _default_timeout:
            last = machine_serial.read()
            #after we have started let's see if the connection is alive
        command = None
        while (not command or command.command_number != -128) and time.clock() - init_start < _default_timeout:
            command = self._read_next_command()
        if not command or command.command_number != -128:
            raise MachineError("Machine does not seem to be ready")
            #ok and if everything is nice we can start a nwe heartbeat thread
        self.last_heartbeat = time.clock()
        self.run_on = True
        self.listening_thread.start()
        self.internal_queue_length = 0
        self.internal_queue_max_length = 1
        self.internal_free_ram = 0

    def send_command(self, command):
        _logger.info("sending command " + str(command))
        #empty the queue?? shouldn't it be empty??
        self.response_queue.empty()
        self.machine_serial.write(str(command.command_number))
        if command.arguments:
            self.machine_serial.write(",")
            for param in command.arguments[:-1]:
                self.machine_serial.write(repr(param))
                self.machine_serial.write(",")
            self.machine_serial.write(repr(command.arguments[-1]))
        self.machine_serial.write(";\n")
        self.machine_serial.flush()
        try:
            response = self.response_queue.get(timeout=_default_timeout)
            #TODO logging
            return response
        except Empty:
            #disconnect in panic
            self.run_on = False
            raise MachineError("Machine does not listen!")


    def last_heart_beat(self):
        if self.last_heartbeat:
            return time.clock() - self.last_heartbeat
        else:
            return None

    def __call__(self, *args, **kwargs):
        while self.run_on:
            command = self._read_next_command()
            if command:
                # if it is just the heart beat we write down the time
                if command.command_number == -128:
                    self.last_heartbeat = time.clock()
                    if command.arguments:
                        self.internal_queue_length = command.arguments[0]
                        self.internal_queue_max_length = command.arguments[1]
                        self.internal_free_ram = command.arguments[2]
                else:
                    #we add it to the response queue
                    self.response_queue.put(command)
                    _logger.info("received command " + str(command))

    def _read_next_command(self):
        line = self._doRead()   # read a ';' terminated line
        if not line or not line.strip():
            return None
        line = line.strip()
        _logger.debug("machine said:\'" + line + "\'")
        command = MachineCommand(line)
        return command

    def _doRead(self):
        buff = self.remaining_buffer
        tic = time.time()
        buff += self.machine_serial.read()

        # you can use if not ('\n' in buff) too if you don't like re
        while ((time.time() - tic) < _default_timeout) and (not _commandEndMatcher.search(buff)):
            buff += self.machine_serial.read()

        if _commandEndMatcher.search(buff):
            split_result = buff.split(';', 1)
            self.remaining_buffer = split_result[1]
            return split_result[0]
        else:
            return ''


class MachineCommand():
    def __init__(self, input_line=None):
        self.command_number = None
        self.arguments = None
        if input_line:
            parts = input_line.strip().split(",")
            if len(parts) > 1:
                try:
                    self.command_number = int(parts[0])
                    if len(parts) > 1:
                        self.arguments = parts[1:]
                except ValueError:
                    _logger.warn("unable to decode command:" + input_line)

    def __repr__(self):
        if self.command_number == 0:
            result = "Acknowledgement "
        if self.command_number < 0:
            if self.command_number > -5:
                result = "Info "
            elif self.command_number > -9:
                result = "Warning "
            elif self.command_number == -9:
                result = "Error "
            elif self.command_number == -128:
                result = "Keep Alive Ping "
            else:
                result = "Unkown "
        else:
            result = "Command "
        if self.command_number is not None:
            result += str(self.command_number)
        if self.arguments:
            result += ": "
            result += str(self.arguments)
        return result


class MachineError(Exception):
    def __init__(self, msg, additional_info=None):
        self.msg = msg
        self.additional_info = additional_info