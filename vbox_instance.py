#!/usr/bin/env python

DOCUMENTATION = '''
---
module: vbox_instance
short_description: Manages a VirtualBox host
description:
   - Manage and provision VirtualBox hosts by cloning.
options:
  vboxmanage:
    description:
      - path to VirtualBox's VBoxManage command
    required: false
    default: '/usr/bin/VBoxManage'
  source_image:
    description:
      - name of the source VM to clone from
    required: true
  target_image:
    description:
      - name for the target VM
    required: true
  memsize:
    description:
      - amount of memory to allocate VM (in MB)
    required: false
    default: 512
  network_type:
    description:
      - networking type
    required: false
    default: bridged
    choices: ['bridged','nat','hostonly']
  state:
    description:
      - create or terminate instances
    required: false
    default: 'running'
    choices: ['running', 'absent']
    '''

EXAMPLES = '''
# Create a clone image called web01.localdomain from a source image called base-image-centos6
- vbox_instance: source_image='base-image-centos6' target_image='web01.localdomain'

# Complete playbook to provision a couple of VMs, set their hostname and install httpd...
- hosts: localhost
  connection: local
  gather_facts: False
  tasks:

    - name: provision instance
      vbox_instance: source_image=packer-virtualbox-base-centos7-1424513286 target_image={{ item }} state=running network_type=bridged
      register: instance_result
      with_items:
        - web01.localdomain
        - web02.localdomain

    - name: Add instance results to host group
      add_host: hostname={{ item.ansible_facts.ipaddress }} groupname=vbox_hosts hostname_to_set={{ item.item }}
      with_items: instance_result.results

- hosts: vbox_hosts
  remote_user: vagrant
  sudo: yes

  pre_tasks:
    - name: set hostname
      hostname: name={{ hostname_to_set }}

  tasks:
    - name: install httpd
      yum: name=httpd state=installed
'''


class VBox():
    def __init__(self, module, vboxmanage, source_image, target_image, memsize, network_type, state):
        self.module = module
        self.vboxmanage = vboxmanage
        self.source_image = source_image
        self.target_image = target_image
        self.memsize = memsize
        self.network_type = network_type
        self.state = state

    @staticmethod
    def escape_spaces(s):
        return s.replace(' ', '\ ')

    @staticmethod
    def exec_command(command):
        p = subprocess.Popen(command, shell=True, executable='/bin/bash',
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        p.wait()
        return p

    @property
    def is_running(self):
        p = self.exec_command(self.vboxmanage + ' list runningvms')
        if p.returncode != 0:
            self.module.fail_json(msg='Error determining if target instance is running')
        for stdoutline in p.stdout.readlines():
            if re.match('^"' + self.target_image + '"', stdoutline):
                return True
        return False

    @property
    def ipaddress(self):
        ipregex = re.compile('^Value: (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\n')
        maxtries = 60
        tries = 0
        command = self.vboxmanage + ' guestproperty get ' + self.target_image + ' /VirtualBox/GuestInfo/Net/0/V4/IP'
        while tries < maxtries:
            p = self.exec_command(command)
            if p.returncode != 0:
                self.module.fail_json(msg='failed on command' + command)
            else:
                ipstdout = p.stdout.read()
                if ipstdout == 'No value set!\n':
                    sleep(1)
                    tries += 1
                elif ipregex.match(ipstdout):
                    return ipregex.search(ipstdout).groups()[0]
                else:
                    self.module.fail_json(msg='Error: unexpected stdout while trying to determine VM ip address')
        self.module.fail_json(msg='Timeout exceeded while trying to get VM ip address')

    def get_vms(self):
        p = self.exec_command(self.vboxmanage + ' list vms')
        if p.returncode != 0:
            self.module.fail_json(msg='Error trying to get VM list')
        vmlist = []
        for stdoutline in p.stdout.readlines():
            vmlist.append(re.search('"(.*)"', stdoutline).groups()[0])
        return vmlist

    def get_snapshots(self, source_image):
        p = self.exec_command(self.vboxmanage + ' snapshot ' + source_image + ' list')
        snapshotlist = []
        for stdoutline in p.stdout.readlines():
            if stdoutline != 'This machine does not have any snapshots\n':
                (snapshot_name, snapshot_uuid) = re.search('Name: (.*) \(UUID: (.*)\)', stdoutline).groups()
                snapshotlist.append({snapshot_name: snapshot_uuid})
        return snapshotlist

    # take a snapshot called "ansible-snapshot" if none exists - this is used for linked cloning
    def snapshot(self, source_image):
        if 'ansible-snapshot' not in [item.keys()[0] for item in self.get_snapshots(source_image)]:
            p = self.exec_command(self.vboxmanage + ' snapshot ' + source_image + ' take ansible-snapshot')
            if p.returncode != 0:
                self.module.fail_json(msg='Error taking snapshot')

    def clone_vm(self):
        source_image = ''
        source_candidate_list = []
        for source_candidate in self.get_vms():
            if re.match('.*' + self.source_image + '.*', source_candidate):
                source_candidate_list.append(source_candidate)
        if len(source_candidate_list) > 1:
            self.module.fail_json(msg='Error: found more than one candidate for source image pattern: ".*'
                                      + self.source_image + '.*"')
        elif len(source_candidate_list) == 0:
            self.module.fail_json(msg='Error: cannot find a single candidate for source image pattern: ".*'
                                      + self.source_image + '.*"')
        else:
            source_image = source_candidate_list[0]
        self.snapshot(source_image)
        p = self.exec_command(self.vboxmanage + ' clonevm ' + source_image +
                              ' --options link --name ' + self.target_image +
                              '  --snapshot ansible-snapshot --register')
        if p.returncode != 0:
            self.module.fail_json(msg='Failed to clone VM')

    def set_network_type(self):
        p = self.exec_command(self.vboxmanage + ' modifyvm ' + self.target_image + ' --nic1 ' + self.network_type)
        if p.returncode != 0:
            self.module.fail_json(msg='Error setting network type')
        # TODO: implement parameterisation of hostonly interface
        if self.network_type == 'hostonly':
            hostonly_interface_regex = re.compile('^Name:\s+(.+)$')
            hostonly_interface_list = []
            p = self.exec_command(self.vboxmanage + ' list hostonlyifs')
            if p.returncode != 0:
                self.module.fail_json(msg='Error: failed to find hostonly interfaces')
            for stdoutline in p.stdout.readlines():
                if hostonly_interface_regex.match(stdoutline):
                    hostonly_interface_list.append(hostonly_interface_regex.search(stdoutline).groups()[0])
            if len(hostonly_interface_list) != 1:
                self.module.fail_json(msg='Error: failed to find exactly 1 matching bridged interface')

            p = self.exec_command(self.vboxmanage + ' modifyvm ' + self.target_image +
                                  ' --hostonlyadapter1 "' + hostonly_interface_list[0] + '"')
            if p.returncode != 0:
                self.module.fail_json(msg='Error setting hostonly adapter')
        # TODO: implement parameterisation of bridged interface (to override non-mac "en0" convention)
        elif self.network_type == 'bridged':
            bridged_interface_regex = re.compile('^Name:\s+(en0.+|eth.+)$')
            bridged_interface_list = []
            p = self.exec_command(self.vboxmanage + ' list bridgedifs')
            if p.returncode != 0:
                self.module.fail_json(msg='Error: failed to find bridged interfaces')
            for stdoutline in p.stdout.readlines():
                if bridged_interface_regex.match(stdoutline):
                    bridged_interface_list.append(bridged_interface_regex.search(stdoutline).groups()[0])
            if len(bridged_interface_list) != 1:
                self.module.fail_json(msg='Error: failed to find exactly 1 matching bridged interface')
            p = self.exec_command(self.vboxmanage + ' modifyvm ' + self.target_image +
                                  ' --bridgeadapter1 "' + bridged_interface_list[0] + '"')

            if p.returncode != 0:
                self.module.fail_json(msg='Error setting bridged adapter')

    def set_memsize(self):
        p = self.exec_command(self.vboxmanage + ' modifyvm ' + self.target_image + ' --memory ' + self.memsize)
        if p.returncode != 0:
            self.module.fail_json(msg='Error: failed to set memory size')

    def start_vm(self):
        if self.target_image not in self.get_vms():
            self.clone_vm()
            self.set_network_type()
            self.set_memsize()
        p = self.exec_command(self.vboxmanage + ' startvm ' + self.target_image + ' --type gui')
        if p.returncode != 0 or self.is_running is False:
            self.module.fail_json(msg='Error trying to start VM')

    def stop_vm(self):
        if self.is_running:
            p = self.exec_command(self.vboxmanage + ' controlvm ' + self.target_image + ' poweroff')
            if p.returncode != 0:
                self.module.fail_json(msg='Failed to power-off VM')

    def delete_vm(self):
        if self.is_running:
            self.stop_vm()
        p = self.exec_command(self.vboxmanage + ' unregistervm ' + self.target_image + ' --delete')
        if p.returncode != 0:
            self.module.fail_json(msg='Failed to delete VM')


def main():
    module = AnsibleModule(
        argument_spec=dict(
            vboxmanage=dict(default='/usr/bin/VBoxManage'),
            source_image=dict(required=True),
            target_image=dict(required=True),
            memsize=dict(default='512'),
            network_type=dict(default='bridged'),
            state=dict(default='running'),
        )
    )

    vboxmanage = module.params["vboxmanage"]
    source_image = module.params["source_image"]
    target_image = module.params["target_image"]
    memsize = module.params["memsize"]
    network_type = module.params["network_type"]
    state = module.params["state"]

    v = VBox(module, vboxmanage, source_image, target_image, memsize, network_type, state)

    if state == 'running':
        msg = 'target instance: ' + target_image + ' running'
        if v.is_running:
            module.exit_json(changed=False, msg=msg, ansible_facts=dict(ipaddress=v.ipaddress))
        else:
            v.start_vm()
            module.exit_json(changed=True, msg=msg, ansible_facts=dict(ipaddress=v.ipaddress))
    if state == 'absent':
        msg = 'target instance: ' + target_image + ' deleted'
        if v.target_image in v.get_vms():
            v.delete_vm()
            module.exit_json(changed=True, msg=msg)
        else:
            module.exit_json(changed=False, msg=msg)


from ansible.module_utils.basic import *
from time import sleep

if __name__ == '__main__':
    main()