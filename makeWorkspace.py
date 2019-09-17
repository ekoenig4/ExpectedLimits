from ROOT import *
import os
from optparse import OptionParser
from array import array
from fitting.createWorkspace import createWorkspace
from fitting.createDatacards import createDatacards
import re
from subprocess import Popen,PIPE,STDOUT

def GetMxlist(sysfile):
    rfile = TFile.Open(sysfile)
    rfile.cd('sr')
    regexp = re.compile(r'Mx\d+_Mv\d+$')
    mxlist = {}
    for sample in gDirectory.GetListOfKeys():
        if regexp.search(sample.GetName()):
            mx = sample.GetName().split('_')[0].replace('Mx','')
            mv = sample.GetName().split('_')[1].replace('Mv','')
            if mx not in mxlist: mxlist[mx] = []
            mxlist[mx].append(mv)
    return mxlist
def makeMxDir(mx,mvlist):
    cwd = os.getcwd()
    regions = ['sr','e','m','ee','mm']
    mxdir = 'Mx_%s' % mx
    print 'Creating %s Directory' % mxdir
    if not os.path.isdir(mxdir): os.mkdir(mxdir)
    os.chdir(mxdir)
    args = ['combineCards.py']
    for region in regions: args.append('%s=../datacard_%s' % (region,region))
    args += ['>','datacard']
    command = ''
    for arg in args: command += '%s ' % arg 
    os.system( command )

    with open('datacard','r') as f: card = f.read()
    card = card.replace('Mx10_Mv1000','Mx%s_$MASS' % mx)
    with open('datacard','w') as f: f.write(card)
    with open('mvlist','w') as f:
        for mv in mvlist:
            f.write(mv+'\n')
    os.chdir(cwd)
#####
def makeWorkspace():
    if not os.path.isdir("Limits/"): os.mkdir("Limits/")
    
    parser = OptionParser()
    parser.add_option("-i","--input",help="Specify input systematics file to generate limits from",action="store",type="str",default=None)
    options,args = parser.parse_args()

    
    mxlist = GetMxlist(options.input)
    fname = options.input.split('/')[-1]
    sysfile = os.path.abspath(options.input)
    ##########################################################
    dir = 'Limits/'+fname.replace('.root', '')
    dir = os.path.abspath(dir)
    if not os.path.isdir(dir): os.mkdir(dir)
    ##################################################
    os.chdir(dir)
    wsfname = 'workspace.root'
    if not os.path.isfile(wsfname): createWorkspace(sysfile)
    createDatacards(wsfname)
    ########################################################
    for mx,mvlist in mxlist.items(): makeMxDir(mx,mvlist)
######################################################################
if __name__ == "__main__": makeWorkspace()
    
