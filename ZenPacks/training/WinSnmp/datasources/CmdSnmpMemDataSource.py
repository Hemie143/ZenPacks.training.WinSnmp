from zope.component import adapts
from zope.interface import implements

from Products.Zuul.form import schema
from Products.Zuul.infos import ProxyProperty
from Products.Zuul.infos.template import RRDDataSourceInfo
from Products.Zuul.interfaces import IRRDDataSourceInfo
from Products.Zuul.utils import ZuulMessageFactory as _t

from ZenPacks.zenoss.PythonCollector.datasources.PythonDataSource import PythonDataSource, PythonDataSourcePlugin

import os
import subprocess
from twisted.internet import defer

# Setup logging
import logging

log = logging.getLogger('zen.PythonWinSnmp')


class CmdSnmpMemDataSource(PythonDataSource):
    """Get RAM and Paging data for Windows devices using SNMP"""

    ZENPACKID = 'ZenPacks.training.WinSnmp'

    # Friendly names for your datasource type in the drop-down selection
    sourcetypes = ('CmdSnmpMemDataSource',)
    sourcetype = sourcetypes[0]

    component = '${here/id}'
    eventClass = '/Perf/Memory/Snmp'
    # cycletime is standard and defaults to 300
    cycletime = 120

    # Custom fields in the datasource - with default values
    # (which can be overridden in template)
    hostname = '${dev/id}'
    ipAddress = '${dev/manageIp}'
    snmpVer = '${dev/zSnmpVer}'
    snmpCommunity = '${dev/zSnmpCommunity}'

    _properties = PythonDataSource._properties + (
        {'id': 'hostname', 'type': 'string', 'mode': 'w'},
        {'id': 'ipAddress', 'type': 'string', 'mode': 'w'},
        {'id': 'snmpVer', 'type': 'string', 'mode': 'w'},
        {'id': 'snmpCommunity', 'type': 'string', 'mode': 'w'},
    )

    # Collection plugin for this type. Defined below in this file
    plugin_classname = ZENPACKID + '.datasources.CmdSnmpMemDataSource.CmdSnmpMemPlugin'

    def addDataPoints(self):
        if not self.datapoints.getOb('MemoryTotal', None):
            self.manage_addRRDDataPoint('MemoryTotal')
        if not self.datapoints.getOb('MemoryUsed', None):
            self.manage_addRRDDataPoint('MemoryUsed')
        if not self.datapoints.getOb('PercentMemoryUsed', None):
            self.manage_addRRDDataPoint('PercentMemoryUsed')
        if not self.datapoints.getOb('PagingTotal', None):
            self.manage_addRRDDataPoint('PagingTotal')
        if not self.datapoints.getOb('PagingUsed', None):
            dp = self.manage_addRRDDataPoint('PagingUsed')
            dp.rrdtype = 'DERIVE'
            dp.rrdmin = 0
            dp.rrdmax = None  # rrdmin must be lower than rrdmax
            dp.description = 'Paging used as a counter'
        if not self.datapoints.getOb('PercentPagingUsed', None):
            self.manage_addRRDDataPoint('PercentPagingUsed')


class ICmdSnmpMemDataSourceInfo(IRRDDataSourceInfo):
    """Interface that creates the web form for this datasource type"""

    hostname = schema.TextLine(title=_t(u'Hostname'), group=_t('CmdSnmpMemDatSource'))
    ipAddress = schema.TextLine(title=_t(u'IP Address'), group=_t('CmdSnmpMemDatSource'))
    snmpVer = schema.TextLine(title=_t(u'SNMP Version'), group=_t('CmdSnmpMemDatSource'))
    snmpCommunity = schema.TextLine(title=_t(u'SNMP Community'), group=_t('CmdSnmpMemDatSource'))
    cycletime = schema.TextLine(title=_t(u'Cycle Time (seconds)'))


class CmdSnmpMemDataSourceInfo(RRDDataSourceInfo):
    """Info - adapter between ICmdSnmpMemDataSourceInfo and CmdSnmpMemDataSourceInfo"""

    implements(ICmdSnmpMemDataSourceInfo)
    adapts(CmdSnmpMemDataSource)

    hostname = ProxyProperty('hostname')
    ipAddress = ProxyProperty('ipAddress')
    snmpVer = ProxyProperty('snmpVer')
    snmpCommunity = ProxyProperty('snmpCommunity')
    cycletime = ProxyProperty('cycletime')

    testable = False  # bugged ?


class CmdSnmpMemPlugin(PythonDataSourcePlugin):
    """Collection plugin class for CmdSnmpMemDataSource"""

    proxy_attributes = ('zSnmpVer',
                        'zSnmpCommunity',
                        )

    @classmethod
    def config_key(cls, datasource, context):
        """
        Returns a tuple defining collection uniqueness

        Classmethod executed by zenhub. Datasource and context arguments are full objects

         Default implementation. Split config id by device, cycletime, template id, datasource id and plugin class

         Optional
        """

        log.debug('In config_key context.device().id is {} \
                    datasource.getCycleTime(context) is {} \
                    datasource.rrdTemplate().id is {} \
                    datasource.id is {} \
                    datasource.plugin_classname is {} '.format(
            context.device().id, datasource.getCycleTime(context), datasource.rrdTemplate().id,
            datasource.id, datasource.plugin_classname))

        return (context.device().id, datasource.getCycleTime(context), datasource.rrdTemplate().id,
                datasource.id, datasource.plugin_classname)

    @classmethod
    def params(cls, datasource, context):
        """
        Returns dictionary of params needed for this plugin

        Classmethod executed by zenhub. Datasource and context arguments are full objects

        scope includes dmd object database and context's attributes and methods
        """

        params = {}
        params['snmpVer'] = datasource. talesEval(datasource.snmpVer, context)
        params['snmpCommunity'] = datasource.talesEval(datasource.snmpCommunity, context)

        # Get path to executable file, starting from datasources to ../libexec
        thisabspath = os.path.abspath(__file__)
        filedir, tail = os.path.split(thisabspath)
        libexecdir = filedir + '/../libexec'

        # script is winmem.py
        cmdparts = [os.path.join(libexecdir, 'winmem.py')]

        # context is object the plugin is applied to, device or component
        if context.manageIp:
            cmdparts.append(context.manageIp)
        elif context.titleOrId():
            cmdparts.append(context.titleOrId())
        else:
            cmdparts.append('UnknownHostOrIp')

        if not params['snmpVer']:
            cmdparts.append('v1')
        else:
            cmdparts.append(params['snmpVer'])

        if not params['snmpCommunity']:
            cmdparts.append('public')
        else:
            cmdparts.append(params['snmpCommunity'])

        params['cmd'] = cmdparts

        log.debug(' params is {} \n'.format(params))
        return params

    def collect(self, config):
        """
        :param config:
        :return: a Twisted deferred
        """
        log.debug(' config is %s \n ' % (config))      ######################################################################
        log.debug(' config.datasources is %s \n ' % (config.datasources))   #################################################

        ds0 = config.datasources[0]
        cmd = ds0.params['cmd']
        log.debug(' cmd is {} \n'.format(cmd))
        cmd_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        cmd_out = cmd_process.communicate()
        dd = defer.Deferred()
        # cmd_process.communicate() returns a tuple of (stdoutdata, stderrordata)
        if cmd_process.returncode == 0:
            dd.callback(cmd_out[0])
        else:
            dd.errback(cmd_out[1])
        return dd

    def onResult(self, result, config):
        log.debug('result is {}'.format(result))

    def onSuccess(self, result, config):
        log.debug('In success - result is {} and config is {}'.format(result, config))

        data = self.new_data()      # Check what this does ########################################################################
        log.debug('In success - data is {} '.format(data))

        dataVarVals = result.split('|')[1].split()
        log.debug('In success - split result is {} '.format(dataVarVals))
        datapointDict = {}
        for d in dataVarVals:
            myvar, myval = d.split('=')
            datapointDict[myvar] = myval
        log.debug('In success - datapointDict is {} '.format(datapointDict))
        data['values'] = {None: datapointDict}

        data['events'].append({
            'device': config.id,
            'summary': 'Snmp memory data gathered using zenpython with winmem script',
            'severity': 1,
            'eventClass': '/App',
            'eventKey': 'PythonCmdSnmpMem',
        })

        data['maps'] = []

        log.debug(' data is {}'.format(data))
        return data

    def onError(self, result, config):
        log.debug(' In onError - result is {} and config is {}'.format(result, config))
        return {
            'events': [{}
                'summary': 'Error getting Snmp memory data with zenpython: {}'.format(result),
                'eventKey': 'PythonCmdSnmpMem',
                'severity': 4,
            }],
        }

    def onComplete(self, result, config):
        return result