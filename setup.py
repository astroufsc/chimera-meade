from distutils.core import setup

setup(
    name='chimera-meade',
    version='0.0.1',
    packages=['chimera_meade', 'chimera_meade.instruments'],
    scripts=[],
    url='http://github.com/astroufsc/chimera-meade',
    license='GPL v2',
    author='William Schoenell',
    author_email='william@iaa.es',
    install_requires=['pyserial'],
    description='Chimera plugin for MEADE telescopes'
)
