#!/usr/bin/env python

import logging
import argparse
import random

from distutils.sysconfig import get_python_lib

from OpenMesher.interfaces import IOpenMesherConfigPlugin, IOpenMesherPackagePlugin, IOpenMesherDeployPlugin
from OpenMesher.lib import nested_dict_merge
from OpenMesher.linkmesh import create_link_mesh
from OpenMesher.tunnelobjects import Mesh

from yapsy.PluginManager import PluginManager


def main():
    # Find and load plugins
    pm = PluginManager()

    libpath = '%s/OpenMesher/plugins' % (get_python_lib())

    pm.setPluginPlaces([
        '/usr/share/openmesher/plugins',
        '~/.openmesher/plugins',
        './OpenMesher/plugins',
        './plugins',
        libpath,
    ])
    pm.setPluginInfoExtension('plugin')
    pm.setCategoriesFilter({
        'config': IOpenMesherConfigPlugin,
        'package': IOpenMesherPackagePlugin,
        'deploy': IOpenMesherDeployPlugin,
    })
    pm.collectPlugins()

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Configure, package, and deploy an OpenVPN mesh"
    )
    parser.add_argument('-r', '--router', action='append', help='Adds a router that can be a client and server')
    parser.add_argument(
        '-s',
        '--server',
        action='append',
        help='Adds a router that can only act as a server, not a client.'
    )
    parser.add_argument(
        '-c',
        '--client',
        action='append',
        help='Adds a router than can only act as a client.  For example, a router that is behind NAT and not '
        'accessible by a public IP'
    )

    # BUG: Stupid argparse appends your switches to the default.
    # parser.add_argument('-n', '--network', action='append', default=['10.99.99.0/24'])
    parser.add_argument('-n', '--network', action='append', required=True)

    portgroup = parser.add_mutually_exclusive_group()
    portgroup.add_argument('-p', '--ports', action='append', default=['7000-7999'])
    portgroup.add_argument('-a', '--random', action='store_true')

    parser.add_argument(
        '-v',
        '--verbose',
        action='append_const',
        const='verbose',
        help='Specify multiple times to make things more verbose'
    )
    parser.add_argument('--version', action='version', version='v0.6.4')

    pluginargsgroup = parser.add_argument_group('plugins')

    for plugin in pm.getAllPlugins():
        plugin.plugin_object.setupargs(pluginargsgroup)

    arg = parser.parse_args()

    for plugin in pm.getAllPlugins():
        if plugin.plugin_object.__class__.__name__.lower() in arg:
            if eval('arg.%s' % (plugin.plugin_object.__class__.__name__.lower())):
                logging.debug('Plugin enabled: %s' % (plugin.name))
                pm.activatePluginByName(plugin.name)
            else:
                logging.debug('Plugin disabled: %s' % (plugin.name))
                pm.deactivatePluginByName(plugin.name)
        else:
            logging.debug('Plugin disabled: %s' % (plugin.name))
            pm.deactivatePluginByName(plugin.name)

    l = logging.getLogger()
    if arg.verbose:
        if len(arg.verbose) == 1:
            l.setLevel(logging.INFO)
        if len(arg.verbose) >= 2:
            l.setLevel(logging.DEBUG)

    # Call activate() on all plugins so they prep themselves for use
    for plugin in pm.getAllPlugins():
        if eval('arg.%s' % (plugin.plugin_object.__class__.__name__.lower())):
            logging.info('Enabled plugin: %s' % (plugin.name))
            pm.activatePluginByName(plugin.name)
            plugin.plugin_object.activate()
            plugin.plugin_object._enabled = True

    if len(arg.ports) > 1:
        arg.ports.reverse()
        arg.ports.pop()

    port_list = []
    if arg.random:
        numdevs = 0
        if arg.router:
            numdevs += len(arg.router)

        if arg.server:
            numdevs += len(arg.server)

        if arg.client:
            numdevs += len(arg.client)

        ports_needed = numdevs * (numdevs - 1) / 2

        for i in range(0, ports_needed):
            port_list.append(random.randrange(1025, 32767))

    try:
        if not arg.random:
            # If we're not using random ports, pull whatever is in arg.ports
            for portrange in arg.ports:
                portstart, portstop = portrange.split('-')
                port_list += range(int(portstart), int(portstop))
    except ValueError as e:
        print 'Invalid port range: %s' % (portrange)
        raise ValueError('Invalid port range: %s' % (portrange))

    linkmesh = create_link_mesh(routers=arg.router, servers=arg.server, clients=arg.client)

    m = Mesh(linkmesh, port_list, arg.network)

    files = None

    # Run through config plugins
    configPlugins = []
    for plugin in pm.getPluginsOfCategory('config'):
        if plugin.plugin_object._enabled:
            plugin.plugin_object.process(m, arg)
            configPlugins.append(plugin.plugin_object)
            if files:
                files = nested_dict_merge(files, plugin.plugin_object.files())
            else:
                files = plugin.plugin_object.files()

    # Grab list of folders that need to be in the package root
    includedirs = []
    for f in files:
        for fldr in files[f]:
            rootfldr = fldr.split('/')[1]
            if rootfldr not in includedirs:
                includedirs.append(rootfldr)

    logging.debug('The following folders will be packaged: %s' % (includedirs))

    # Run through packaging plugins
    packagePlugins = []
    for plugin in pm.getPluginsOfCategory('package'):
        if plugin.plugin_object._enabled:
            # BUG: Services to restart may not necessarily be the same name as their config dir...
            plugin.plugin_object.process(
                m,
                include_dirs=includedirs,
                restart_services=includedirs,
                configPlugins=configPlugins,
                cliargs=arg
            )
            packagePlugins.append(plugin.plugin_object)

    # Run through deployment plugins
    for plugin in pm.getPluginsOfCategory('deploy'):
        if plugin.plugin_object._enabled:
            try:
                plugin.plugin_object.deploy(packagePlugins=packagePlugins, cliargs=arg, stoponfailure=False)
            except Exception as e:
                print "Unable to deploy due to error: %s" % (e)

    logging.info('OpenMesher run complete')


if __name__ == "__main__":
    main()
