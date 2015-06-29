chimera-serial plugin
=====================

A chimera_ plugin for a bunch of serial bus devices.

Usage
-----

Install chimera_ on your computer, and then, this package. Edit the configuration file adding one of the serial
supported devices as on the example below.

Installation
------------

Besides chimera_, ``chimera-serial`` depends only of pyserial_.

::

    pip install -U git+https://github.com/astroufsc/chimera-serial.git


Configuration Examples
----------------------

Here goes examples of the configuration to be added on ``chimera.config`` file.

* `MEADE LX200`_ telescope

::

    telescope:
        name: lx200
        type: Meade
        device: /dev/ttyS0    # can be COM1 on Windows

* `JMI Smart 232`_ focuser

::

    focuser:
      name: jmismart
      type: JMIsmart232
      device: COM3          # /dev/ttyS?? on linux

* `Optec TCF-S`_ focuser

::

    focuser:
      type: OptecTCFS
      name: optec
      device: COM6          # /dev/ttyS?? on linux

* LNA_ 40cm dome

::

    dome:
     type: DomeLNA40cm
     name: dome
     device: COM7
     telescope: 200.131.64.200:7666/TheSkyTelescope/paramount
     telescope: /FakeTelescope/fake
     model: COTE/LNA


Tested Hardware
---------------

This plugin was tested on these hardware:

* `JMI Smart 232`_ focuser
* MEADE LX200 16'' telescope
* `Optec TCF-S`_ focuser
* LNA 40cm dome. This dome is a custom build.


Contact
-------

For more information, contact us on chimera's discussion list:
https://groups.google.com/forum/#!forum/chimera-discuss

Bug reports and patches are welcome and can be sent over our GitHub page:
https://github.com/astroufsc/chimera-serial/

.. _chimera: https://www.github.com/astroufsc/chimera/
.. _pyserial: http://pyserial.sourceforge.net/
.. _JMI Smart 232: http://www.jimsmobile.com/
.. _LNA: http://www.lna.br/
.. _MEADE LX200: http://www.meade.com/products/telescopes/lx200.html
.. _Optec TCF-S: http://www.optecinc.com/astronomy/catalog/tcf/tcf-s.htm