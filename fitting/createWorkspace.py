#!/usr/bin/env python
from ROOT import *
from SysFile import *
from theory_sys import getTFShift
import os
import re
import json

gSystem.Load("libHiggsAnalysisCombinedLimit.so")

def irange(lo,hi): return range(lo,hi+1)
def validHisto(hs,total=0,threshold=0.2): return hs.Integral() > threshold*total
def validShape(up,dn): return any( up[ibin] != dn[ibin] for ibin in range(1,up.GetNbinsX()+1) ) and validHisto(up) and validHisto(dn)

def getReciprocal(histo):
    reciprocal = histo.Clone( histo.GetName() + "_reciprocal" )
    reciprocal.Divide(histo);
    reciprocal.Divide(histo);
    return reciprocal
def getFractionalShift(norm,up,dn,reciprocal=False):
    sh = up.Clone( up.GetName().replace('Up','') ); sh.Reset()

    if reciprocal:
        up = getReciprocal(up)
        dn = getReciprocal(dn)
    for ibin in irange(1,sh.GetNbinsX()):
        if norm[ibin] != 0:
            upshift = up[ibin]/norm[ibin] - 1.
            dnshift = dn[ibin]/norm[ibin] - 1.
            shiftEnvelope = max( abs(upshift),abs(dnshift) )
        else: shiftEnvelope = 0
        sh[ibin] = shiftEnvelope
    return sh

def getAverageShift(norm,up,dn,reciprocal=False):
    sh = up.Clone( up.GetName().replace('Up','Avg') ); sh.Reset()

    if reciprocal:
        up = getReciprocal(up)
        dn = getReciprocal(dn)
    for ibin in irange(1,sh.GetNbinsX()):
        if norm[ibin] != 0:
            upshift = up[ibin] - norm[ibin]
            dnshift = dn[ibin] - norm[ibin]
            shiftEnvelope = 0.5 * (upshift + dnshift)
        else: shiftEnvelope = 0
        sh[ibin] = shiftEnvelope
    return sh

def getShift(norm,up,dn,reciprocal=False):
    sh = up.Clone( up.GetName().replace('Up','Shift') ); sh.Reset()
    if reciprocal:
        up = getReciprocal(up)
        dn = getReciprocal(dn)
    for ibin in irange(1,sh.GetNbinsX()):
        if norm[ibin] != 0:
            upshift = up[ibin] - norm[ibin]
            dnshift = dn[ibin] - norm[ibin]
            shiftEnvelope = 0.5 * (upshift - dnshift)
        else: shiftEnvelope = 0
        sh[ibin] = shiftEnvelope
    return sh

class BinList:
    store = []
    def __init__(self,template,sysdir,var,setConst=False):
        self.template = template
        self.procname = template.procname + '_model'
        self.sysdir = sysdir
        self.sysdir.cd()
        self.var = var
        # self.obs = self.sysdir.Get(self.procname).Clone("%s_%s"%(self.procname,self.sysdir.GetTitle()))
        self.obs = self.template.obs.Clone("%s_%s"%(self.procname,self.sysdir.GetTitle()))
        self.nuisances = self.template.nuisances
        self.binlist = RooArgList()
        for i in irange(1,self.obs.GetNbinsX()):
            bin_name = "%s_%s_bin_%i" % (self.procname,self.sysdir.GetTitle(),i-1)
            bin_label = "%s Yield in %s, bin %i" % (self.procname,self.sysdir.GetTitle(),i-1)
            bin_yield = self.obs.GetBinContent(i)
            if setConst: nbin = RooRealVar(bin_name,bin_label,bin_yield)
            else:
                nbin = RooRealVar(bin_name,bin_label,bin_yield,0.,2*bin_yield)
                nbin.removeMax()
            self.binlist.add(nbin)
            self.store.append(nbin)
        self.p_bkg = RooParametricHist(self.obs.GetName(),"%s PDF in %s"%(self.procname,self.sysdir.GetTitle()),self.var,self.binlist,self.obs)
        self.p_bkg_norm = RooAddition("%s_norm"%self.obs.GetName(),"%s total events in %s"%(self.procname,self.sysdir.GetTitle()),self.binlist)
    def Export(self,ws):
        ws.Import(self.p_bkg,RooFit.RecycleConflictNodes())
        ws.Import(self.p_bkg_norm,RooFit.RecycleConflictNodes())

class ConnectedBinList(BinList):
    apply_theory = ("wsr_to_zsr","ga_to_sr")
    theory_correlation = {
        "QCD_Scale":True,
        "QCD_Shape":True,
        "QCD_Proc":True,
        "NNLO_Sud":False,
        "NNLO_Miss":False,
        "NNLO_EWK":True,
        "QCD_EWK_Mix":True,
        "PDF":True
    }
    store = []
    def power_syst(n,nominal,first,second=0): return "(TMath::Power(1+{first},@{n}))".format(**vars())
    def linear_syst(n,nominal,first,second=0):
        if second == 0:
            return "(1 + ({first}*@{n})/{nominal})".format(**vars())
        return "(1 + ({second}*@{n}*@{n}+{first}*@{n})/{nominal})".format(**vars())
    def __init__(self,template,sysdir,var,tf_proc,tf_channel):
        self.tf_proc = tf_channel.bkgmap[ tf_proc[template.procname] + '_model' ]
        self.tfname = tf_proc[id]
        self.template = template
        self.procname = self.template.procname + '_model'
        self.sysdir = sysdir
        self.sysdir.cd()
        self.var = var

        # self.bkg_tf = self.sysdir.Get('transfer/%s'%self.tfname).Clone("%s_%s"%(self.procname,self.sysdir.GetTitle()))
        self.obs = self.template.obs.Clone("%s_%s_obs"%(self.procname,self.sysdir.GetTitle()))
        self.nuisances = self.template.nuisances
        
        # template / tf_proc
        self.bkg_tf = self.obs.Clone("%s_%s"%(self.procname,self.sysdir.GetTitle()))
        self.bkg_tf.Divide(self.tf_proc.obs)

        # tf_proc / template: used to get the uncertainties
        self.bkg_tf_re = self.tf_proc.obs.Clone("%s_%s_re"%(self.procname,self.sysdir.GetTitle()))
        self.bkg_tf_re.Divide(self.obs)
        
        self.binlist = RooArgList()
        
        self.addSystFromTemplate()
        for i in irange(1,self.bkg_tf.GetNbinsX()):
            ibin = i-1
            bin_name = "%s_bin%i" % (self.bkg_tf.GetName(),ibin)
            bin_label = "%s TF Ratio, bin %i" % (self.bkg_tf.GetName(),ibin)
            bin_ratio = self.bkg_tf.GetBinContent(i)

            formula_binlist = RooArgList()
            tfbin = self.tf_proc.binlist[i-1]
            nbin = RooRealVar("r_"+bin_name,bin_label,bin_ratio)
            self.store.append(nbin)
            
            formula_binlist.add(tfbin)
            formula_binlist.add(nbin)
            num = "@0" # tf_proc yield
            den = "@1" # template/tf_proc yield            
            
            j = -1
            for j,syst in enumerate(self.systs.values()):
                systform = self.getSystFormula(bin_ratio,syst["envelope"][i],syst[RooRealVar],nbin=ibin)
                formula_binlist.add( systform )
                den += "*@%i" % (j+2)
            statvar = RooRealVar("%s_stat_bin%i" % (self.bkg_tf.GetName(),ibin),"%s TF Stats, bin %i" % (self.bkg_tf.GetName(),ibin),0.,-4.,4.)
            self.store.append(statvar)
            statform = self.getSystFormula(bin_ratio,self.bkg_tf.GetBinError(i)/bin_ratio,statvar)
            formula_binlist.add(statform)
            den += "*@%i" % (j+3)
            
            formula = "%s * (%s)"%(num,den)
            bin_formula = RooFormulaVar(bin_name,bin_label,formula,formula_binlist)
            self.binlist.add(bin_formula)
            self.store.append(bin_formula)
        self.p_bkg = RooParametricHist(self.bkg_tf.GetName(),"%s PDF in %s"%(self.procname,self.sysdir.GetTitle()),self.var,self.binlist,self.bkg_tf)
        self.p_bkg_norm = RooAddition("%s_norm"%self.bkg_tf.GetName(),"%s total events in %s"%(self.procname,self.sysdir.GetTitle()),self.binlist)
    def getSystFormula(self,nominal,systval,systvar,nbin=None,syst_function=power_syst):
        equation = syst_function(0,nominal,systval)
        name = "func_"+systvar.GetName()
        if nbin is not None: name += "_bin%i" % nbin
        formula = RooFormulaVar(name,"Function "+systvar.GetTitle(),equation,RooArgList(systvar))
        self.store.append(formula)
        return formula
    def addSystFromTemplate(self,fromSys=True):
        self.systs = {}
        if self.tfname not in self.apply_theory: return
        for nuisance in self.theory_correlation:
            if nuisance not in self.theory_correlation: continue
            if not fromSys: self.addSyst(nuisance,correlated=self.theory_correlation[nuisance])
            else: self.addFromSys(nuisance,correlated=self.theory_correlation[nuisance])
    def addSysShape(self,up,dn,reciprocal=True):
        if not validShape(up,dn): return
        envelope = getFractionalShift(self.bkg_tf,up,dn,reciprocal)
        average_envelope = getAverageShift(self.bkg_tf,up,dn,reciprocal)
        shift_envelope = getShift(self.bkg_tf,up,dn,reciprocal)
        systvar = RooRealVar(envelope.GetName(),"%s TF Ratio"%envelope.GetName(),0.,-4.,4.)
        self.systs[envelope.GetName()] = {RooRealVar:systvar,"envelope":envelope,"first":shift_envelope,"second":average_envelope}
    def addFromSys(self,syst,correlated=True):
        # sys directory in the form -> tf_proc / template
        if correlated:
            scaleUp,scaleDn = getTFShift(self.tfname,syst)
            up = self.bkg_tf_re.Clone("%s_%sUp"%(self.tfname,syst))
            dn = self.bkg_tf_re.Clone("%s_%sDown"%(self.tfname,syst))
            up.Multiply(scaleUp); dn.Multiply(scaleDn)
            self.addSysShape(up,dn)
        else:
            for part in self.tfname.split("_to_"):
                syst_part = syst+'_'+part
                scaleUp,scaleDn = getTFShift(self.tfname,syst_part)
                up = self.bkg_tf_re.Clone("%s_%sUp"%(self.tfname,syst_part))
                dn = self.bkg_tf_re.Clone("%s_%sDown"%(self.tfname,syst_part))
                up.Multiply(scaleUp); dn.Multiply(scaleDn)
                self.addSysShape(up,dn)
    def addSyst(self,syst,correlated=True):
        # template / tf_proc

            
        num_syst = self.template.nuisances[syst]
        den_syst = self.tf_proc.nuisances[syst]
        if correlated:
            up = num_syst['up'].obs.Clone("%s_%sUp"%(self.tfname,syst))
            dn = num_syst['dn'].obs.Clone("%s_%sDown"%(self.tfname,syst))
            up.Divide(den_syst['up'].obs)
            dn.Divide(den_syst['dn'].obs)
            self.addSysShape(up,dn)
        else:
            numvar,denvar = self.tfname.split("_to_")
            numup = num_syst['up'].obs.Clone("%s_%s_%sUp"%(self.tfname,syst,numvar))
            numdn = num_syst['dn'].obs.Clone("%s_%s_%sDown"%(self.tfname,syst,numvar))
            numup.Divide(self.tf_proc.obs)
            numdn.Divide(self.tf_proc.obs)
            self.addSysShape(numup,numdn)
            
            denup = self.obs.Clone("%s_%s_%sUp"%(self.tfname,syst,denvar))
            dendn = self.obs.Clone("%s_%s_%sDown"%(self.tfname,syst,denvar))
            denup.Divide(den_syst['up'].obs)
            dendn.Divide(den_syst['dn'].obs)
            self.addSysShape(denup,dendn)
class Nuisance:
    def __init__(self,procname,obs,varlist):
        self.procname = procname
        self.varlist = varlist
        
        self.obs = obs
        self.hist = RooDataHist(self.obs.GetName(),"%s Observed"%self.obs.GetName(),self.varlist,self.obs)
    def Export(self,ws): ws.Import(self.hist)
class Template:
    def __init__(self,procname,sysdir,varlist):
        self.procname = procname
        self.sysdir = sysdir
        self.sysdir.cd()
        self.varlist = varlist

        self.obs = self.sysdir.Get(self.procname).Clone("%s_%s"%(self.procname,self.sysdir.GetTitle()))
        self.hist = RooDataHist(self.obs.GetName(),"%s Observed"%self.obs.GetName(),self.varlist,self.obs)

        if 'Up' in self.procname or 'Down' in self.procname: return
        
        self.nuisances = { nuisance.replace(self.procname+'_',"").replace("Up",""):None
                           for nuisance in self.sysdir.keylist
                           if re.match('^'+self.procname,nuisance) and 'Up' in nuisance }
        
        for nuisance in self.nuisances.keys():
            up = self.sysdir.Get("%s_%sUp"%(self.procname,nuisance)).Clone("%s_%s_%sUp"%(self.procname,self.sysdir.GetTitle(),nuisance))
            dn = self.sysdir.Get("%s_%sDown"%(self.procname,nuisance)).Clone("%s_%s_%sDown"%(self.procname,self.sysdir.GetTitle(),nuisance))
            if not validShape(up,dn): continue
            self.nuisances[nuisance] = {'up':Nuisance(up.GetName(),up,self.varlist),'dn':Nuisance(dn.GetName(),dn,self.varlist)}
    def Export(self,ws):
        if not validHisto(self.obs): return
        ws.Import(self.hist)
        for nuisance in self.nuisances.values():
            if not nuisance: continue
            for variation in ('up','dn'): nuisance[variation].Export(ws)
class Channel:
    majormap = {
        "sr":"ZJets" # Need to generate binlist for sr zjets so that it can be used in other connected bin lists
    }
    def __init__(self,syscat,sysdir,signals=[],tf_proc={},tf_channel=None):
        if any(tf_proc) and tf_channel is None: tf_channel = self
        self.bkglist = ["ZJets","DYJets","WJets","GJets","QCD","DiBoson","TTJets"]
        self.syscat = syscat
        self.sysdir = syscat.GetRegion(sysdir)
        self.sysdir.keylist = [ key.GetName() for key in self.sysdir.GetListOfKeys() ]
        self.sysdir.cd()

        self.data = Template('data_obs',self.sysdir,self.syscat.varlist)
        self.bkgmap = {}
        for bkg in list(self.bkglist):
            self.bkgmap[bkg] = Template(bkg,self.sysdir,self.syscat.varlist)
            if bkg in tf_proc:
                self.bkgmap[bkg+'_model'] = ConnectedBinList(self.bkgmap[bkg],self.sysdir,self.syscat.var,tf_proc,tf_channel)
                self.bkglist.append(bkg+'_model')
            elif self.sysdir.GetName() in self.majormap and bkg == self.majormap[self.sysdir.GetName()]:
                self.bkgmap[bkg+'_model'] = BinList(self.bkgmap[bkg],self.sysdir,self.syscat.var)
                self.bkglist.append(bkg+'_model')
        if not any(signals): return
        self.signals = list(signals)
        self.signalmap = { signal:Template(signal,self.sysdir,self.syscat.varlist) for signal in signals }
    def Export(self,ws):
        self.data.Export(ws)
        for bkg in self.bkglist: self.bkgmap[bkg].Export(ws)
        if not hasattr(self,'signals'): return
        for signal in self.signals: self.signalmap[signal].Export(ws)
        
class Workspace(RooWorkspace):
    def __init__(self,*args,**kwargs):
        RooWorkspace.__init__(self,*args,**kwargs)
        self.Import = getattr(self,'import')
    def SignalRegion(self,syscat,signals):
        syscat.sr = Channel(syscat,'sr',signals,tf_proc={"WJets":"ZJets",id:"wsr_to_zsr"})
        syscat.sr.Export(self)
    def SingleEleCR(self,syscat):
        syscat.we = Channel(syscat,'we',tf_proc={"WJets":"WJets",id:"we_to_sr"},tf_channel=syscat.sr)
        syscat.we.Export(self)
    def SingleMuCR(self,syscat):
        syscat.wm = Channel(syscat,'wm',tf_proc={"WJets":"WJets",id:"wm_to_sr"},tf_channel=syscat.sr)
        syscat.wm.Export(self)
    def DoubleEleCR(self,syscat):
        syscat.ze = Channel(syscat,'ze',tf_proc={"DYJets":"ZJets",id:"ze_to_sr"},tf_channel=syscat.sr)
        syscat.ze.Export(self)
    def DoubleMuCR(self,syscat):
        syscat.zm = Channel(syscat,'zm',tf_proc={"DYJets":"ZJets",id:"zm_to_sr"},tf_channel=syscat.sr)
        syscat.zm.Export(self)
    def GammaCR(self,syscat):
        syscat.ga = Channel(syscat,'ga',tf_proc={"GJets":"ZJets",id:"ga_to_sr"},tf_channel=syscat.sr)
        syscat.ga.Export(self)
    def MetaData(self,syscat):
        h_lumi = TH1F("lumi","lumi",1,0,1)
        h_lumi.SetBinContent(1,float(syscat.lumi))
        h_lumi.Write()
        h_year = TH1F("year","year",1,0,1)
        h_year.SetBinContent(1,float(syscat.year))
        h_year.Write()
        syscat.var.Write()
def createWorkspace(syscat,outfname='workspace.root',isScaled=True):

    output = TFile(outfname,"recreate")
    ws = Workspace("w","w")

    signals = ['axial']
    ws.SignalRegion(syscat,signals)
    ws.SingleEleCR(syscat)
    ws.SingleMuCR(syscat)
    ws.DoubleEleCR(syscat)
    ws.DoubleMuCR(syscat)
    ws.GammaCR(syscat)

    output.cd()
    ws.MetaData(syscat)
    ws.Write()
    syscat.ws = ws
    return ws
    
if __name__ == "__main__":
    sysfile = SysFile("/nfs_scratch/ekoenig4/MonoJet/2018/CMSSW_10_2_13/src/HiggsAnalysis/CombinedLimit/monoJetLimits/Systematics/monojet_recoil.sys.root")
    syscat = sysfile.categories["category_monojet_2017"]
    output = TFile("workspace.root","recreate")
    ws = Workspace("w","w")

    signals = ['axial']
    ws.SignalRegion(syscat,signals)
    ws.SingleEleCR(syscat)
    ws.SingleMuCR(syscat)
    ws.DoubleEleCR(syscat)
    ws.DoubleMuCR(syscat)
    ws.GammaCR(syscat)

    output.cd()
    ws.MetaData(syscat)
    ws.Write()
    
