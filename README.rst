chimera-meade plugin
====================

A chimera_ plugin for MEADE_ telescopes 

Usage
-----

Install chimera_ on your computer, and then, this package. Edit the configuration file adding one of the
supported MEADE telescopes as on the example below.

Installation
------------

Besides chimera_, ``chimera-meade`` depends only of pyserial_.

::

    pip install -U git+https://github.com/astroufsc/chimera-meade.git


Configuration Examples
----------------------

Here goes examples of the configuration to be added on ``chimera.config`` file.

* `MEADE LX200`_ telescope

::

    telescope:
        name: lx200
        type: Meade
        device: /dev/ttyS0    # can be COM1 on Windows


Tested Hardware
---------------

This plugin was tested on these hardware:

* MEADE LX200 16'' telescope


Contact
-------

For more information, contact us on chimera's discussion list:
https://groups.google.com/forum/#!forum/chimera-discuss

Bug reports and patches are welcome and can be sent over our GitHub page:
https://github.com/astroufsc/chimera-meade/

.. _chimera: https://www.github.com/astroufsc/chimera/
.. _pyserial: http://pyserial.sourceforge.net/
.. _JMI Smart 232: http://www.jimsmobile.com/
.. _LNA: http://www.lna.br/
.. _MEADE LX200: http://www.meade.com/products/telescopes/lx200.html
.. _MEADE: http://www.meade.com/
.. _Optec TCF-S: http://www.optecinc.com/astronomy/catalog/tcf/tcf-s.htm
