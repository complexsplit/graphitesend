#!/usr/bin/env python

import time
import socket
import os
import pickle
import struct
import requests
import json
_module_instance = None
__version__ = "0.2.0"

default_graphite_server = 'graphite'
default_graphite_plaintext_port = 2003
default_graphite_pickle_port = 2004
default_graphite_event_port = 80


class GraphiteSendException(Exception):
    pass


def split_tags(tags):
    "split up tags from a string into a list"

    if isinstance(tags, str) or isinstance(tags, unicode):
        if ',' in tags:
            tags = tags.split(',')
        else:
            tags = tags.split()
            
    return tags


class GraphiteClient(object):
    """
    Graphite Client that will setup a TCP connection to graphite.

    :param prefix: string added to the start of all metrics
    :type prefix: Default: "systems."
    :param graphite_server: hostname or ip address of graphite server
    :type graphite_server: Default: graphite
    :param graphite_port: TCP port we will connect to
    :type graphite_port: Default: 2003
    :param debug: Toggle debug messages
    :type debug: True or False
    :param group: string added to after prefix and before system_name
    :param system_name: FDQN of the system generating the metrics
    :type system_name: Default: current FDQN
    :param suffix: string added to the end of all metrics
    :param lowercase_metric_names: Toggle the .lower() of all metric names
    :param dryrun: Toggle if it will really send metrics or just print them
    :type dryrun: True or False

    It will then send any metrics that you give it via
    the .send() or .send_dict().

    You can also take advantage of the prefix, group and system_name options
    that allow you to setup default locations where your whisper files will
    be kept.
    eg.
    ( where linuxserver is the name of the localhost)

    .. code-block:: python

      >>> init().prefix
      systems.linuxserver.

      >>> init(system_name='remote_host').prefix
      systems.thumper.

      >>> init(group='cpu').prefix
      systems.linuxserver.cpu.

      >>> init(prefix='apache').prefix
      apache.

    """
    def __init__(self, prefix=None, graphite_server=None, graphite_port=2003,
                 debug=False, group=None, system_name=None, suffix=None,
                 lowercase_metric_names=False, connect_on_create=True,
                 dryrun=False):
        """ 
        setup the connection to the graphite server and work out the
        prefix.

        This allows for very simple syntax when sending messages to the
        graphite server.

        """


        # If we are not passed a host, then use the graphite server defined
        # in the module.
        if not graphite_server:
            graphite_server = default_graphite_server
        self.addr = (graphite_server, graphite_port)


        # If this is a dry run, then we do not want to configure a connection
        # or try and make the connection once we create the object.
        self.dryrun = dryrun
        if self.dryrun:
            self.addr = None
            graphite_server = None
            connect_on_create = False

        # Only connect to the graphite server and port if we tell you too.
        # This is mosty used for testing.
        if connect_on_create:
            self.socket = self.connect()

        self.debug = debug
        self.lastmessage = None

        self.lowercase_metric_names = lowercase_metric_names

        if system_name is None:
            system_name = os.uname()[1]

        if prefix is None:
            prefix = "systems.%(system_name)s" % locals()

        if prefix:
            prefix = prefix + "."

        if group:
            prefix = prefix + "." + group

        # remove double dots
        if '..' in prefix:
            prefix = prefix.replace('..', '.')

        # Replace ' 'spaces with _
        if ' ' in prefix:
            prefix = prefix.replace(' ', '_')

        if suffix:
            self.suffix = suffix
        else:
            self.suffix = ""

        self.prefix = prefix

    def connect(self):
        """
        Make a TCP connection to the graphite server on port self.port
        """
        timeout_in_seconds = 2
        local_socket = socket.socket()
        local_socket.settimeout(timeout_in_seconds)
        try:
            local_socket.connect(self.addr)
        except socket.timeout:
            raise GraphiteSendException(
                "Took over %d second(s) to connect to %s" %
                (timeout_in_seconds, self.addr))
        except socket.gaierror:
            raise GraphiteSendException(
                "No address assoicated with hostname %s:%s" % self.addr)
        except Exception as error:
            raise GraphiteSendException(
                "unknown exception while connecting to %s - %s" %
                (self.addr, error)
            )

        return local_socket

    def clean_metric_name(self, metric_name):
        """
        Make sure the metric is free of control chars, spaces, tabs, etc.
        
        """
        metric_name = metric_name.replace('(', '_').replace(')', '')
        metric_name = metric_name.replace(' ', '_').replace('-', '_')
        return metric_name

    def disconnect(self):
        """
        Close the TCP connection with the graphite server.
        """
        try:
            self.socket.shutdown(1)

        # If its currently a socket, set it to None
        except AttributeError:
            self.socket = None

        # Set the self.socket to None, no matter what.
        finally:
            self.socket = None

    def _send(self, message):
        """ 
        Given a message send it to the graphite server.
        """

        if self.dryrun:
            return message

        if not self.socket:
            raise GraphiteSendException(
                "Socket was not created before send"
            )

        try:
            self.socket.sendall(message)

        # Capture missing socket.
        except socket.gaierror as error:
            raise GraphiteSendException(
                "Failed to send data to %s, with error: %s" %
                (self.addr, error))

        # Capture socket closure before send.
        except socket.error as error:
            raise GraphiteSendException(
                "Socket closed before able to send data to %s, with error: %s" %
                (self.addr, error)
            )

        except Exception as error:
            raise GraphiteSendException(
                "Unknown error while tring to send data down socket to %s, error: %s" %
                (self.addr, error)
            )

        return "sent %d long message: %s" % \
            (len(message), "".join(message[:75]))

    def _presend(self, message):
        """
        Complete any message alteration tasks before sending to the graphite server.
        """
        # An option to lowercase the entire message
        if self.lowercase_metric_names:
            message = message.lower()
        return message

    def send(self, metric, value, timestamp=None):
        """
        Format a single metric/value pair, and send it to the graphite
        server.

        :param metric: name of the metric
        :type prefix: string 
        :param value: value of the metric
        :type prefix: float or int 
        :param timestmap: epoch time of the event
        :type prefix: float or int 

        .. code-block:: python

          >>> g = init()
          >>> g.send("metric", 54)
        
        .. code-block:: python

          >>> g = init()
          >>> g.send(metric="metricname", value=73)

        """
        if timestamp is None:
            timestamp = int(time.time())
        else:
            timestamp = int(timestamp)

        if type(value).__name__ in ['str', 'unicode']:
            value = float(value)

        message = "%s%s%s %f %d\n" % (self.prefix, metric, self.suffix,
                                      value, timestamp)

        message = self. _presend(message)

        if self.dryrun:
            return message

        return self._send(message)

    def send_dict(self, data, timestamp=None):
        """ 
        Format a dict of metric/values pairs, and send them all to the
        graphite server.

        :param data: key,value pair of metric name and metric value
        :type prefix: dict 
        :param timestmap: epoch time of the event
        :type prefix: float or int 

        .. code-block:: python

          >>> g = init()
          >>> g.send_dict({'metric1': 54, 'metric2': 43, 'metricN': 999})

        """
        if timestamp is None:
            timestamp = int(time.time())
        else:
            timestamp = int(timestamp)

        metric_list = []

        for metric, value in data.items():
            if type(value).__name__ in ['str', 'unicode']:
                value = float(value)
            tmp_message = "%s%s%s %f %d\n" % (self.prefix, metric,
                                              self.suffix, value, timestamp)
            metric_list.append(tmp_message)

        message = "".join(metric_list)
        return self._send(message)

    def send_list(self, data, timestamp=None):
        """ 

        Format a list of set's of (metric, value) pairs, and send them all
        to the graphite server.

        :param data: list of key,value pairs of metric name and metric value
        :type prefix: list 
        :param timestmap: epoch time of the event
        :type prefix: float or int 

        .. code-block:: python

          >>> g = init()
          >>> g.send_list([('metric1', 54), ('metric2', 43, 1384418995), ('metricN', 999)])

        """
        if timestamp is None:
            timestamp = int(time.time())
        else:
            timestamp = int(timestamp)

        metric_list = []

        for metric_info in data:

            # Support [ (metric, value, timestamp), ... ] as well as
            # [ (metric, value), ... ].
            # If the metric_info provides a timestamp then use the timestamp.
            # If the metric_info fails to provide a timestamp, use the one
            # provided to send_list() or generated on the fly by time.time()
            if len(metric_info) == 3:
                (metric, value, metric_timestamp) = metric_info
            else:
                (metric, value) = metric_info
                metric_timestamp = timestamp

            if type(value).__name__ in ['str', 'unicode']:
                print "metric='%(metric)s'  value='%(value)s'" % locals()
                value = float(value)

            tmp_message = "%s%s%s %f %d\n" % (self.prefix, metric,
                                              self.suffix, value, metric_timestamp)
            metric_list.append(tmp_message)

        message = "".join(metric_list)
        return self._send(message)


class GraphitePickleClient(GraphiteClient):

    def __init__(self, *args, **kwargs):
        # If the user has not given a graphite_port, then use the default pick
        # port.
        if 'graphite_port' not in kwargs:
            kwargs['graphite_port'] = default_graphite_pickle_port

        # TODO: Fix this hack and use super.
        self = GraphiteClient(*args, **kwargs)

    def str2listtuple(self, string_message):
        "Covert a string that is ready to be sent to graphite into a tuple"

        if type(string_message).__name__ not in ('str', 'unicode'):
            raise TypeError("Must provide a string or unicode")

        tpl_list = []
        for line in string_message.split('\n'):
            try:
                (path, metric, timestamp) = line.split()
            except ValueError:
                raise ValueError(
                    "message must contain - pain, metric and timestamp")
            try:
                timestamp = float(timestamp)
            except ValueError:
                raise ValueError("Timestamp must be float or int")

            tpl_list.append((path, (timestamp, metric)))

        payload = pickle.dumps(tpl_list)
        header = struct.pack("!L", len(payload))
        message = header + payload

        return message

    def _send(self, message):
        """ Given a message send it to the graphite server. """

        # An option to lowercase the entire message
        if self.lowercase_metric_names:
            message = message.lower()

        # convert the message into a pickled payload.
        message = self.str2listtupe(message)

        try:
            self.socket.sendall(message)

        # Capture missing socket.
        except socket.gaierror as error:
            raise GraphiteSendException(
                "Failed to send data to %s, with error: %s" %
                (self.addr, error))

        # Capture socket closure before send.
        except socket.error as error:
            raise GraphiteSendException(
                "Socket closed before able to send data to %s, with error: %s" %
                (self.addr, error))

        except Exception as error:
            raise GraphiteSendException(
                "Unknown error while tring to send data down socket to %s, error: %s" %
                (self.addr, error))

        return "sent %d long pickled message: %s" % len(message)

class GraphiteEventClient(GraphiteClient):


    def __init__(self, graphite_server=None, graphite_port=None,
                 debug=False, metic=None, lowercase_metric_names=False, 
                 tags=None, dryrun=False):

        # If we are not passed a host, then use the graphite server defined
        # in the module.
        if not graphite_server:
            graphite_server = default_graphite_server
        if not graphite_port:
            graphite_port = default_graphite_event_port

        # Enable debug messages
        self.debug = debug

        uri_scheme = 'http'
        if graphite_port in (443, ):
            uri_scheme = 'https'
            
        self.remote_uri = "%s://%s:%s/events/" % \
                          (uri_scheme, graphite_server, graphite_port)

    def send_list(self, data, tags=None, timestamp=None):
        "Send a list of events to the remote graphit server."

        for event in data:
            if isinstance(event, str) or isinstance(tags, unicode):
                self.send(metric=event, tags=None, timestamp=None)
            if isinstance(event, dict):
                event_payload = {
                    'tags': tags,
                    'timestamp': timestamp
                    }
                if 'tags' in event:
                    event_payload['tags'] = split_tags(event['tags']) + split_tags(tags)
                if 'tmestamp' in event:
                    event_payload['timestamp'] = timestmap

                event_payload['metric'] = event['metric']
                self.send(**event_payload)
                
    def send_dict(self, data, tags=None, timestamp=None):
        "send a dict of key, data to the remote graphite server."


        for event_name, event_data in data.items():
            if isinstance(event_data, str) or isinstance(event_data, unicode):
                self.send(metric=event_name, value=event_data, tags=tags, timestamp=timestamp)
                continue
            if isinstance(event_data, dict):
                event_payload = {
                    'tags': tags,
                    }

                if timestamp:
                    event_payload['timestamp'] = timestamp

                if 'tags' in event_data:
                    event_payload['tags'] = split_tags(event_data['tags']) + split_tags(tags)
                if 'tmestamp' in event_data:
                    event_payload['timestamp'] = timestmap

                if 'value' in event_data:
                    event_payload['value'] = event_data['value']

                event_payload['metric'] = event_name

                self.send(**event_payload)
                continue
            raise NotImplemented("Unknown type to process")
            

    def send(self, metric, value=None, tags=None, timestamp=None):
        "Send a single event to the remote graphite server."

        event_payload = {
            "what": metric
        }

        # If we have a timestmap, validate and pass it on
        if timestamp is not None:
            timestamp = int(time.time())
            event_payload["when"] = timestamp

        # For django-taggit we will be very clear to keep the tags seperate
        if tags:
            event_payload["tags"] = " , ".join(split_tags(tags))

        if value:
            event_payload["data"] = value

        event_payload = json.dumps(event_payload)
        post_response = requests.post(self.remote_uri, data=event_payload)
        return post_response.status_code



def init(init_type='plaintext_tcp', *args, **kwargs):
    """
    Create the module instance of the GraphiteClient.
    """
    global _module_instance
    reset()

    validate_init_types = ['plaintext_tcp', 'plaintext', 'pickle_tcp',
                           'pickle', 'plain', 'event']

    if init_type not in validate_init_types:
        raise GraphiteSendException(
            "Invalidte init_type '%s', must be one of: %s" %
            (init_type, ", ".join(validate_init_types)))

    # Use TCP to send data to the plain text reciever on the graphite server.
    if init_type in ['plaintext_tcp', 'plaintext', 'plain']:
        _module_instance = GraphiteClient(*args, **kwargs)

    # Use TCP to send pickled data to the pickle reciver on the graphite
    # server.
    if init_type in ['pickle_tcp', 'picke']:
        _module_instance = GraphiteClient(*args, **kwargs)

    # Use the Event system
    if init_type in ('event',):
        _module_instance = GraphiteEventClient(*args, **kwargs)

    return _module_instance


def send(*args, **kwargs):
    """ Make sure that we have an instance of the GraphiteClient.
    Then send the metrics to the graphite server.
    User consumable method.
    """
    if not _module_instance:
        raise GraphiteSendException(
            "Must call graphitesend.init() before sending")

    _module_instance.send(*args, **kwargs)
    return _module_instance


def send_dict(*args, **kwargs):
    """ Make sure that we have an instance of the GraphiteClient.
    Then send the metrics to the graphite server.
    User consumable method.
    """
    if not _module_instance:
        raise GraphiteSendException(
            "Must call graphitesend.init() before sending")
    _module_instance.send_dict(*args, **kwargs)
    return _module_instance


def send_list(*args, **kwargs):
    """ Make sure that we have an instance of the GraphiteClient.
    Then send the metrics to the graphite server.
    User consumable method.
    """
    if not _module_instance:
        raise GraphiteSendException(
            "Must call graphitesend.init() before sending")
    _module_instance.send_list(*args, **kwargs)
    return _module_instance


def reset():
    """ disconnect from the graphite server and destroy the module instance.
    """
    global _module_instance
    if not _module_instance:
        return False
    _module_instance.disconnect()
    _module_instance = None


def cli():
    """ Allow the module to be called from the cli. """
    import argparse

    parser = argparse.ArgumentParser(description='Send data to graphite')

    # Is this an event?
    parser.add_argument('--event', '-e', dest='event', action='store_true', 
                        help='Store in graphite event system, not timeseries data')
    # Core of the application is to accept a metric and a value.
    parser.add_argument('metric', metavar='metric', type=str,
                        help='name.of.metric')
    parser.add_argument('value', metavar='value', type=str,
                        help='value of metric as int')

    args = parser.parse_args()
    is_event = args.event
    metric = args.metric
    value = args.value

    init_type='plaintext_tcp'
    if is_event:
        init_type='event'
        

    graphitesend_instance = init(init_type=init_type)
    graphitesend_instance.send(metric, value)

if __name__ == '__main__':
    cli()
