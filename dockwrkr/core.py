import os
import sys
import logging
import re
from dockwrkr.monads import *
from dockwrkr.logs import *
from dockwrkr.exceptions import *
from dockwrkr.utils import (readYAML, mergeDict, ensureList, dateToAgo, walkUpForFile)
import dockwrkr.docker as docker

logger = logging.getLogger(__name__)

class Core(object):

  def __init__(self):
    self.options = {}
    self.configFile = 'dockwrkr.yml'
    self.dockerClient = 'docker'
    self.pidsDir = None
    self.initialized = False
    self.config = {}
    return

  def configDefaults(self):
    if self.config.get('pids'):
      self.pidsDir = self.config.get('pids')
    if self.config.get('docker'):
      self.dockerClient = self.config.get('docker')

  def initialize(self):
    if self.initialized:
      return OK(None)
    return self.loadConfig().then(self.configDefaults).then(defer(self.setInitialized, b=True))

  def setInitialized(self, b):
    self.initialized = b

  def loadConfig(self):
    return self.readConfigFile() >> self.setConfig

  def findConfigFile(self):
    configFile = walkUpForFile(os.getcwd(), "dockwrkr.yml")
    if not configFile:
      return Fail(ConfigFileNotFound("Could not locate config file: dockwrkr.yml"))
    self.configFile = configFile
    return OK(configFile)
 
  def readConfigFile(self):
    return self.findConfigFile().bind(lambda f: Try.attempt(readYAML, f))

  def setConfig(self, config):
    mergeDict(self.config, config)
    return OK(self)

  def getDefinedContainers(self):
    graph = []
    containers = self.config.get('containers')
    if not containers:
      containers = self.config
      self.legacyConfig = True

    for container in containers.keys():
      node = self.getContainerDependencies(container)
      graph.append( node )

    def resolveDependencies(node, resolved):
      for dep in node['deps']:
        depnode = self.getContainerDependencies(dep)
        if depnode['name'] not in resolved:
          resolveDependencies(depnode, resolved)
      if node['name'] not in resolved:
        resolved.append(node['name'])
    resolved = []
    for node in graph:
      resolveDependencies(node, resolved)

    return resolved

  def getContainerDependencies(self, container):
    node = {}
    node['name'] = container
    deps = []
    config = self.getContainerConfig(container)
    if "link" in config:
      for link in ensureList( config['link'] ):
        deps.append( link.partition(':')[0] )
    node['deps'] = deps
    return node

  def getBasePath(self):
    return os.path.dirname(self.configFile)

  def getContainerConfig(self, container):
    return self.config.get('containers', {}).get(container)

  def getContainerImage(self, container):
    conf = self.getContainerConfig(container)
    return conf.get('image', None)

  ### Commands ###

  def readOrderedContainers(self, containers=[]):
    defined = self.getDefinedContainers()
    missing = [x for x in containers if x not in defined]
    ordered = [x for x in defined if x in containers]
    if missing:
      return Fail(InvalidContainerError("Container '%s' not defined." % ' '.join(missing)))
    return OK(ordered)

  def start(self, containers=[], all=False):
    return self.__command(self.__start, containers=containers, all=all)

  def stop(self, containers=[], all=False, time=docker.DOCKER_STOP_TIME):
    return self.__command(self.__stop, containers=containers, all=all, time=time)

  def remove(self, containers=[], all=False, time=docker.DOCKER_STOP_TIME, force=False):
    return self.__command(self.__remove, containers=containers, all=all, time=time, force=force)

  def restart(self, containers=[], all=False, time=docker.DOCKER_STOP_TIME):
    return self.__command(self.__restart, containers=containers, all=all, time=time)

  def status(self, containers=[]):
    if not containers:
      containers = self.getDefinedContainers()
    return self.__readStates(containers) \
      .bind(self.__status, containers=containers)

  def __status(self, state, containers=[]):
    table = []
    for container in containers:
      if container not in state:
        status = docker.ContainerStatus(container)
      else:
        status = state[container]

      row = [
        container, 
        status.getCol('cid'), 
        status.getCol('pid'), 
        status.getCol('ip'),
        dateToAgo(status.startedat) if status.startedat else "-",
        docker.getErrorLabel(status) if not status.running else "-"
      ]
      table.append(row)
    return OK(table) 

  def reset(self, time=docker.DOCKER_STOP_TIME):
    managed = docker.readManagedContainers()
    if managed.isFail():
      return managed

    return managed \
      .bind(docker.filterExistingContainers) \
      .bind(docker.readContainersStatus) \
      .bind(self.__remove, containers=managed.getOK(), time=time, force=True)

  def pull(self, containers=[], all=False):
    if all:
      containers = self.getDefinedContainers()

    def pullImage(container):
      image = self.getContainerImage(container)
      return docker.pull(image).then(dinfo("'%s' (%s) has been pulled." % (container, image)))

    return Try.sequence(map(pullImage, containers))

  def recreate(self, containers=[], all=False, time=docker.DOCKER_STOP_TIME):
    if all:
      containers = self.getDefinedContainers()
    return self.__readStates(containers) \
      .bind(self.__remove, containers=containers, force=True, time=time) \
      .then(defer(self.__readStates, containers=containers)) \
      .bind(self.__start, containers=containers)
   
  def __command(self, func, containers=[], all=False, *args, **kwargs):
    if all:
      containers = self.getDefinedContainers()
    return self.__readStates(containers) \
      .bind(func, containers=containers, *args, **kwargs)

  def __readStates(self, containers):
    return self.readOrderedContainers(containers) \
      .bind(docker.filterExistingContainers) \
      .bind(docker.readContainersStatus) 

  def __start(self, state, containers=[]):
    ops = []
    for container in containers:
      if container not in state:
        op = docker.create(container, self.getContainerConfig(container), basePath=self.getBasePath()) \
          .then(defer(docker.start, container=container)) \
          .then(dinfo("'%s' has been created and started." % container)) 
        ops.append(op)
      else:
        if not state[container].running:
          op = docker.start(container).bind(dinfo("'%s' has been started." % container))
          ops.append(op)
        else:
          logger.warn("'%s' is already running." % container)
    return Try.sequence(ops)

  def __stop(self, state, containers=[], time=docker.DOCKER_STOP_TIME):
    ops = []
    for container in containers:
      if container not in state:
        logger.warn("Container '%s' does not exist." % container)
      else:
        if state[container].running:
          ops.append( docker.stop(container, time).bind(dinfo("'%s' has been stopped." % container)) )
        else:
          logger.warn("'%s' is not running." % container)
    return Try.sequence(ops)

  def __remove(self, state, containers=[], force=False, time=docker.DOCKER_STOP_TIME):
    logger.debug("REMOVE %s" % containers)
    ops = []
    for container in containers:
      if container in state:
        if state[container].running:
          if not force:
            logger.error("'%s' is running and 'force' was not specified." % container)
          else:
            op = docker.stop(container, time=time) \
              .then(defer(docker.remove, container=container)) \
              .bind(dinfo("'%s' has been stopped and removed." % container))
            ops.append(op)
        else:    
          ops.append(docker.remove(container).bind(dinfo("'%s' has been removed." % container)))
    return Try.sequence(ops)

  def __restart(self, state, containers=[], time=docker.DOCKER_STOP_TIME):
    ops = []
    for container in containers:
      if container not in state:
        logger.error("'%s' does not exist." % container)
      else:
        if state[container].running:
          op = docker.stop(container, time=time) \
            .then(defer(docker.start, container=container)) \
            .bind(dinfo("'%s' has been restarted." % container))
          ops.append(op)
        else:
          ops.append(docker.start(container).bind(dinfo("'%s' has been started." % container)))

    return Try.sequence(ops)
