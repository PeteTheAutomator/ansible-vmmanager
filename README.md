ansible-vmmanager
=================

Description
-----------
An Ansible module for managing Virtual Machines on personal virtualisers - ie: VMWare Fusion and VirtualBox.

Example
-------
An example playbook to provision a couple of VirtualBox VMs with bridged networking, set their hostname and install httpd...

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


Contact
-------

Support
-------

Installation
------------
Simply copy fusion_instance.py and/or vbox_instance.py into (for example) ~/myansiblemodules and add that path to the ANSIBLE_LIBRARY environment variable.

TODO
----
* a general code tidy
