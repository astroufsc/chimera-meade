from distutils.core import setup

setup(
    name='chimera-serial',
    version='0.0.1',
    packages=['chimera_serial', 'chimera_serial.instruments'],
    scripts=[],
    url='http://github.com/astroufsc/chimera-serial',
    license='GPL v2',
    author='William Schoenell',
    author_email='william@iaa.es',
    install_requires=['pyserial'],
    description='Chimera plugin for serial devices'
)
