# coding=utf-8
from Queue import Queue
from copy import deepcopy
import logging
from math import sqrt, copysign
from numpy import sign
from threading import Thread
from trinamic_3d_printer.Machine import Machine
from trinamic_3d_printer.helpers import _convert_mm_to_steps, find_shortest_vector, _calculate_relative_vector

__author__ = 'marcus'
_logger = logging.getLogger(__name__)


class Printer(Thread):
    def __init__(self, serial_port, reset_pin, print_min_length=50, print_max_length=100):
        Thread.__init__(self)
        self.ready = False
        self.printing = False
        self.config = None
        self.axis = {'x': {}, 'y': {}}
        self.axis['x']['motor'] = None
        self.axis['y']['motor'] = None
        self.axis['x']['scale'] = None
        self.axis['y']['scale'] = None
        self.axis['x']['max_speed'] = None
        self.axis['y']['max_speed'] = None
        self.axis['x']['max_acceleration'] = None
        self.axis['y']['max_acceleration'] = None
        self.axis['x']['bow'] = None
        self.axis['y']['bow'] = None

        self.printer_thread = None
        self._print_queue = None
        self.print_queue_min_length = print_min_length
        self.print_queue_max_length = print_max_length

        self._homing_timeout = 10

        #finally create and conect the machine
        self.machine = Machine(serial_port=serial_port, reset_pin=reset_pin)
        _logger.debug("Connecting printer")
        self.machine.connect()

    def configure(self, config):
        if not config:
            raise PrinterError("No printer config given!")

        self._configure_axis(self.axis['x'], config["x-axis"])
        self._configure_axis(self.axis['y'], config["y-axis"])

        printer_config = config['printer']
        if "print_queue" in printer_config:
            print_queue_config = printer_config["print-queue"]
            self.print_queue_min_length = print_queue_config['min-length']
            self.print_queue_max_length = print_queue_config['max-length']
        if "homing-timeout" in printer_config:
            self._homing_timeout = printer_config['homing-timeout']

        self.config = config

        self.ready = True

    def start_print(self):
        self.machine.batch_mode = True
        self._print_queue = PrintQueue(axis_config=self.axis, min_length=self.print_queue_min_length,
                                       max_length=self.print_queue_max_length)
        self.printing = True
        self.start()

    def stop_print(self):
        self._print_queue.finish()
        self.printing = False
        pass

    def home(self, axis):
        for home_axis in axis:
            _logger.info("Homing axis \'%s\' to zero", home_axis)
            #read the homing config for the axis
            home_speed = self.axis[home_axis]['home_speed']
            home_precision_speed = self.axis[home_axis]['home_precision_speed']
            home_acceleration = self.axis[home_axis]['home_acceleration']
            #convert everything from mm to steps
            home_speed = _convert_mm_to_steps(home_speed, self.axis[home_axis]['steps_per_mm'])
            home_precision_speed = _convert_mm_to_steps(home_precision_speed, self.axis[home_axis]['steps_per_mm'])
            home_acceleration = _convert_mm_to_steps(home_acceleration, self.axis[home_axis]['steps_per_mm'])
            #make a config out of it
            homing_config = {
                'motor': self.axis[home_axis]['motor'],
                'timeout': 0,
                'home_speed': home_speed,
                'home_slow_speed': home_precision_speed,
                'home_retract' : retract,
                'acceleration': home_acceleration,
                'deceleration': home_acceleration,
                'start_bow': self.axis[home_axis]['bow_step'],
                'end_bow': self.axis[home_axis]['bow_step'],
            }
            #and do the homing
            self.machine.home(homing_config, timeout=self._homing_timeout)
            #better but still not good - we should have a better concept of 'axis'
        self.x_pos = 0
        self.y_pos = 0


    # tuple with x/y/e coordinates - if left out no change is intended
    def move_to(self, position):
        self._print_queue.add_movement(position)

    def run(self):
        while self.printing:
            #get the next movement from stack
            movement = self._print_queue.next_movement()

            delta_x, delta_y, move_vector, step_pos, step_speed_vector = self._add_movement_calculations(movement)

            x_move_config, y_move_config = self._generate_move_config(movement, step_pos, step_speed_vector)

            self._move(delta_x, delta_y, move_vector, step_pos, x_move_config, y_move_config)

    def _configure_axis(self, axis, config):
        axis['steps_per_mm'] = config['steps-per-mm']

        axis['motor'] = config['motor']
        axis['scale'] = config['steps-per-mm']
        axis['max_speed'] = config['max-speed']
        axis['max_speed_step'] = _convert_mm_to_steps(config['max-speed'], config['steps-per-mm'])
        axis['max_acceleration'] = config['max-acceleration']
        axis['max_step_acceleration'] = _convert_mm_to_steps(config['max-acceleration'], config['steps-per-mm'])
        axis['bow'] = config['bow-acceleration']
        axis['bow_step'] = _convert_mm_to_steps(config['bow-acceleration'], config['steps-per-mm'])

        if 'home-speed' in config:
            axis['home_speed'] = config['home-speed']
        else:
            axis['home_speed'] = config['max-speed']
        if 'home-precision-speed' in config:
            axis['home_precision_speed'] = config['home-precision-speed']
        else:
            axis['home_precision_speed'] = config['max-speed']/10
        if 'home_acceleration' in config:
            axis['home_acceleration'] = config['home-acceleration']
        else:
            axis['home_acceleration'] = config['max-acceleration']


        axis['end-stops'] = {}
        end_stops_config = config['end-stops']
        for end_stop_pos in ('left', 'right'):
            if end_stop_pos in end_stops_config:
                end_stop_config = end_stops_config[end_stop_pos]
                polarity = end_stop_config['polarity']
                if 'virtual' == polarity:
                    position = float(end_stop_config['position'])
                    axis['end-stops'][end_stop_pos] = {
                        'type': 'virtual',
                        'position': position
                    }
                elif polarity in ('positive', 'negative'):
                    axis['end-stops'][end_stop_pos] = {
                        'type': 'real',
                        'polarity': polarity
                    }
                else:
                    raise PrinterError("Unknown end stop type " + polarity)
                end_stop = deepcopy(axis['end-stops'][end_stop_pos])
                if 'position' in end_stop:
                    end_stop['position'] = _convert_mm_to_steps(end_stop['position'], axis['scale'])
                self.machine.configure_endstop(motor=axis['motor'], position=end_stop_pos, end_stop_config=end_stop)

        motor = config["motor"]
        current = config["current"]
        self.machine.set_current(motor, current)

    def _add_movement_calculations(self, movement):
        step_pos = {
            'x': _convert_mm_to_steps(movement['x'], self.axis['x']['scale']),
            'y': _convert_mm_to_steps(movement['y'], self.axis['y']['scale'])
        }
        step_speed_vector = {
            'x': _convert_mm_to_steps(movement['speed']['x'], self.axis['x']['scale']),
            'y': _convert_mm_to_steps(movement['speed']['y'], self.axis['y']['scale'])
        }
        delta_x = movement['delta_x']
        delta_y = movement['delta_y']
        move_vector = movement['relative_move_vector']
        return delta_x, delta_y, move_vector, step_pos, step_speed_vector

    def _generate_move_config(self, movement, step_pos, step_speed_vector):
        def _axis_movement_template(axis):
            return {
                'motor': axis['motor'],
                'acceleration': axis['max_step_acceleration'],
                'deceleration': axis['max_step_acceleration'],
                'startBow': axis['bow_step'],
                'endBow': axis['bow_step']
            }

        x_move_config = _axis_movement_template(self.axis['x'])
        x_move_config['target'] = step_pos['x']
        x_move_config['speed'] = abs(step_speed_vector['x'])
        if 'x_stop' in movement:
            x_move_config['type'] = 'stop'
        else:
            x_move_config['type'] = 'way'

        y_move_config = _axis_movement_template(self.axis['y'])
        y_move_config['target'] = step_pos['y']
        y_move_config['speed'] = abs(step_speed_vector['y'])
        if 'y_stop' in movement:
            y_move_config['type'] = 'stop'
        else:
            y_move_config['type'] = 'way'

        return x_move_config, y_move_config

    def _move(self, delta_x, delta_y, move_vector, step_pos, x_move_config, y_move_config):
        if delta_x and not delta_y: #silly, but simpler to understand
            #move x motor
            _logger.debug("Moving X axis to %s", step_pos['x'])

            self.machine.move_to([
                x_move_config
            ])

        elif delta_y and not delta_x: # still silly, but stil easier to understand
            #move y motor to position
            _logger.debug("Moving Y axis to %s", step_pos['y'])

            self.machine.move_to([
                y_move_config
            ])
        elif delta_x and delta_y:
            #ok we have to see which axis has bigger movement
            if abs(delta_x) > abs(delta_y):
                y_factor = abs(move_vector['y'] / move_vector['x'])
                _logger.debug(
                    "Moving X axis to %s gearing Y by %s to %s"
                    , step_pos['x'], y_factor, step_pos['y'])

                y_move_config['acceleration'] *= y_factor
                y_move_config['deceleration'] *= y_factor
                self.machine.move_to([
                    x_move_config,
                    y_move_config
                ])
                #move
            else:
                x_factor = abs(move_vector['x'] / move_vector['y'])
                _logger.debug(
                    "Moving Y axis to %s gearing X by %s  to %s"
                    , step_pos['x'], x_factor, step_pos['y'])
                x_move_config['acceleration'] *= x_factor
                x_move_config['deceleration'] *= x_factor
                self.machine.move_to([
                    x_move_config,
                    y_move_config
                ])


class PrintQueue():
    def __init__(self, axis_config, min_length, max_length, default_target_speed=None):
        self.axis = axis_config
        self.planning_list = list()
        self.queue_size = min_length - 1 #since we got one extra
        self.queue = Queue(maxsize=(max_length - min_length))
        self.previous_movement = None
        #we will use the last_movement as special case since it may not fully configured
        self.default_target_speed = default_target_speed

    def add_movement(self, target_position, timeout=None):
        move = {}
        #calculate the target
        self._extract_movement_values(move, target_position)
        #and see how fast we can allowable go
        move['max_achievable_speed_vector'] = self._maximum_achievable_speed(move)
        #and since we do not know it better the first guess is that the final speed is the max speed
        move['speed'] = move['max_achievable_speed_vector']
        #now we can push the previous move to the queue and recalculate the whole queue
        if self.previous_movement:
            self.planning_list.append(self.previous_movement)
            #if the list is long enough we can give it to the queue so that readers can get it
        if len(self.planning_list) > self.queue_size:
            self._push_from_planning_to_execution(timeout)
        self.previous_movement = move
        #and recalculate the maximum allowed speed
        self._recalculate_move_speeds(move)

    def next_movement(self, timeout=None):
        return self.queue.get(timeout=timeout)

    def finish(self, timeout=None):
        while len(self.planning_list) > 0:
            self._push_from_planning_to_execution(timeout)
        while not self.queue.empty():
            pass

    def _push_from_planning_to_execution(self, timeout):
        executed_move = self.planning_list.pop(0)
        self.queue.put(executed_move, timeout=timeout)
        #todo this is just debug - but easier to debug so - you know what I mean
        _logger.info("adding to execution queue, now at %s/%s entries", len(self.planning_list), self.queue.qsize())

    def _extract_movement_values(self, move, target_position):
        #extract values
        if 'x' in target_position:
            move['x'] = target_position['x']
        else:
            if self.previous_movement:
                move['x'] = self.previous_movement['x']
            else:
                move['x'] = 0
        if 'y' in target_position:
            move['y'] = target_position['y']
        else:
            if self.previous_movement:
                move['y'] = self.previous_movement['y']
            else:
                move['y'] = 0
        if 'f' in target_position:
            move['target_speed'] = target_position['f']
        elif self.previous_movement:
            move['target_speed'] = self.previous_movement['target_speed']
        elif self.default_target_speed:
            move['target_speed'] = self.default_target_speed
        else:
            raise PrinterError("movement w/o a set speed and no default speed is set!")
        if self.previous_movement:
            last_x = self.previous_movement['x']
            last_y = self.previous_movement['y']
        else:
            last_x = 0
            last_y = 0
            #logg this move
        if _logger.isEnabledFor(logging.DEBUG) and ('x' in move or 'y' in move):
            log_text = "moving to "
            if 'x' in move:
                log_text += "X:" + str(move['x']) + " "
            if 'y' in move:
                log_text += "Y:" + str(move['y']) + " "
            _logger.debug(log_text)

        move['delta_x'] = move['x'] - last_x
        move['delta_y'] = move['y'] - last_y
        move_vector = _calculate_relative_vector(move['delta_x'], move['delta_y'])
        #save the move vector for later use …
        move['relative_move_vector'] = move_vector

    def _maximum_achievable_speed(self, move):
        if self.previous_movement:
            last_x_speed = self.previous_movement['speed']['x']
            last_y_speed = self.previous_movement['speed']['y']
        else:
            last_x_speed = 0
            last_y_speed = 0
        delta_x = move['delta_x']
        delta_y = move['delta_y']
        move_vector = move['relative_move_vector']
        #derrive the various speed vectors from the movement … for desired head and maximum axis speed
        speed_vectors = [
            {
                # add the desired speed vector as initial value
                'x': move['target_speed'] * move_vector['x'],
                'y': move['target_speed'] * move_vector['y']
            }
        ]
        if delta_x != 0:
            scaled_y = move_vector['y'] / move_vector['x']
            speed_vectors.append({
                #what would the speed vector for max x speed look like
                'x': copysign(self.axis['x']['max_speed'], move_vector['x']),
                'y': self.axis['x']['max_speed'] * copysign(scaled_y, move_vector['y'])
            })
            if not self.previous_movement or sign(delta_x) == sign(self.previous_movement['delta_x']):
                #ww can accelerate further
                max_speed_x = last_x_speed ** 2 + 2 * self.axis['x']['max_acceleration'] * delta_x
                max_speed_x = copysign(sqrt(abs(max_speed_x)), max_speed_x)# little trick to have a proper sign
                speed_vectors.append({
                    #how fast can we accelerate in X direction anyway
                    'x': max_speed_x,
                    'y': max_speed_x * scaled_y
                })
            else:
                #we HAVE to turn around!
                if self.previous_movement:
                    self.previous_movement['x_stop'] = True
                max_speed_x = 2 * self.axis['x']['max_acceleration'] * delta_x
                max_speed_x = copysign(sqrt(abs(max_speed_x)), max_speed_x)# little trick to have a proper sign
                speed_vectors.append({
                    #how fast can we accelerate in X direction anyway
                    'x': max_speed_x,
                    'y': max_speed_x * scaled_y
                })
        else:
            #we HAVE to turn around!
            if self.previous_movement:
                self.previous_movement['x_stop'] = True

        if delta_y != 0:
            scaled_y = move_vector['x'] / move_vector['y']
            speed_vectors.append({
                #what would the maximum speed vector for y movement look like
                'x': self.axis['y']['max_speed'] * scaled_y,
                'y': copysign(self.axis['y']['max_speed'], move_vector['y'])
            })
            if not self.previous_movement or sign(delta_y) == sign(self.previous_movement['delta_y']):
                #ww can accelerate further
                max_speed_y = last_y_speed ** 2 + 2 * self.axis['y']['max_acceleration'] * delta_y
                max_speed_y = copysign(sqrt(abs(max_speed_y)), max_speed_y)
                speed_vectors.append({
                    #how fast can we accelerate in X direction anyway
                    'x': max_speed_y * scaled_y,
                    'y': max_speed_y
                })
            else:
                #we HAVE to turn around!
                if self.previous_movement:
                    self.previous_movement['y_stop'] = True
                max_speed_y = 2 * self.axis['y']['max_acceleration'] * delta_y
                max_speed_y = copysign(sqrt(abs(max_speed_y)), max_speed_y)
                speed_vectors.append({
                    #how fast can we accelerate in X direction anyway
                    'x': max_speed_y * scaled_y,
                    'y': max_speed_y
                })
        else:
            #we HAVE to turn around!
            if self.previous_movement:
                self.previous_movement['y_stop'] = True

        max_local_speed_vector = find_shortest_vector(speed_vectors)
        #the minimum achievable speed is the minimum of all those local vectors

        return max_local_speed_vector


    def _recalculate_move_speeds(self, move):
        max_speed = move['speed']
        for movement in reversed(self.planning_list):
            #todo in theory we can stop somewhere …
            delta_x = movement['delta_x']
            if sign(delta_x) == sign(max_speed['x']):
                max_speed_x = max_speed['x'] ** 2 + 2 * self.axis['x']['max_acceleration'] * delta_x
            else:
                max_speed_x = 2 * self.axis['x']['max_acceleration'] * delta_x
            max_speed_x = copysign(sqrt(abs(max_speed_x)), max_speed_x)# little trick to have a proper sign
            delta_y = movement['delta_y']
            if sign(delta_y) == sign(max_speed['y']):
                max_speed_y = max_speed['y'] ** 2 + 2 * self.axis['y']['max_acceleration'] * delta_y
            else:
                max_speed_y = 2 * self.axis['y']['max_acceleration'] * delta_y
            max_speed_y = copysign(sqrt(abs(max_speed_y)), max_speed_y)# little trick to have a proper sign
            speed_vectors = [
                movement['speed']
            ]
            move_vector = movement['relative_move_vector']
            if move_vector['x'] != 0:
                speed_vectors.append({
                    #what would the speed vector for max x speed look like
                    'x': max_speed_x,
                    'y': max_speed_x * move_vector['y'] / move_vector['x']
                })
            if move_vector['y'] != 0:
                speed_vectors.append({
                    #what would the speed vector for max x speed look like
                    'x': max_speed_y * move_vector['x'] / move_vector['y'],
                    'y': max_speed_y
                })
            movement['speed'] = find_shortest_vector(speed_vectors)
            max_speed = movement['speed']


class PrinterError(Exception):
    def __init__(self, msg):
        self.msg = msg

