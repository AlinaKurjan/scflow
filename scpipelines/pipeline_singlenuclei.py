##############################################################################
#
#   Botnar Resaerch Centre
#
#   $Id$
#
#   Copyright (C) 2018 Adam Cribbs
#
#   This program is free software; you can redistribute it and/or
#   modify it under the terms of the GNU General Public License
#   as published by the Free Software Foundation; either version 2
#   of the License, or (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software
#   Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
###############################################################################

"""
====================
Pipeline single cell
====================


Overview
==================

This pipeline performs alignment free based quantification of single-nuclei-seq.
The pipeline is based off a biostars response by lior pachter:
https://www.biostars.org/p/397671/.

The nuclei data will be processed using kalliso and a custom built DNA and intron
index for the species of interest. Two matrices will be generated: one for
spliced transcripts and one for unspliced transcripts, which will be summed
to obtain the total nuclear transcripts.

To learn how to generate a cDNA and intron index see the following tutorial:
https://www.kallistobus.tools/velocity_index_tutorial.html.

Important: The mouse cDNA and intron index is about 26GB. Because of this,
building it and processing data with it requires significantly more RAM than
typical kallisto workflows, and we recomend using a machine with at least
64GB RAM for this workflow.

The downstream analysis of the data is then almost identical to the singlecell
pipeline.

Usage
=====

Configuration
-------------

The pipeline uses CGAT-core and CGAT-apps throughout the pipeline. Please see installation
and setup and installation instructions at `cgat-core documentation <>`_


Input files
-----------

The pipeline is ran using fastq files that follow the naming convention Read1: Name.fastq.1.gz
and read2: Name.fastq.2.gz.

 * a fastq file (single /paired end (always paired end for drop seq methods)
 * a GTF geneset

For drop-seq, the default file format assumes the following convention:
fastq.1.gz and fastq.2.gz for paired data, where fastq.1.gz contains UMI/cellular barcode data and fastq.2.gz contains sequencing reads.
Chromium outputis of the format: samplename_R1.fastq.gz and samplename_R2.fastq.gz so will require conversion to the default file format above.

Pipeline output
==================

The output of running this pipeline is the generation of an
Rmarkdown report with summary statistics. The end user can use this as a base
to then further develop project specific analyses.

Code
==================

"""
from ruffus import *

import sys
import os
import re
import sqlite3

import cgatcore.pipeline as P
import cgatcore.experiment as E
import scpipelines.ModuleSC

import pandas as pd

import cgat.GTF as GTF
import cgatcore.iotools as iotools

# Load options from the config file

PARAMS = P.get_parameters(
    ["%s/pipeline.yml" % os.path.splitext(__file__)[0],
     "../pipeline.yml",
     "pipeline.yml"])

# Determine the location of the input fastq files

try:
    PARAMS['data']
except NameError:
    DATADIR = "."
else:
    if PARAMS['data'] == 0:
        DATADIR = "."
    elif PARAMS['data'] == 1:
        DATADIR = "data.dir"
    else:
        DATADIR = PARAMS['data']

SEQUENCESUFFIXES = ("*.fastq.gz",
		    "*.fastq.1.gz")
SEQUENCEFILES = tuple([os.path.join(DATADIR, suffix_name)
                       for suffix_name in SEQUENCESUFFIXES])

############################################
# Build indexes
############################################
# Need to build one index for cDNA and one for intron

# Determine read length - can do this in python
'''
$ zcat R1.fastq.gz | head -2 ## note on a mac you would do zcat < R1.fastq.gz | head
@SRR8742283.1 NS500422:552:HJ5Y3BGX3:1:11101:21875:1038 length=61
CAGTCNTTTTTTTTAATTTAAAAAAAAAAAAAAGATTTATTAACAGTTTTAGAAGGCAGTT

$ echo -n CAGTCNTTTTTTTTAATTTAAAAAAAAAAAAAAGATTTATTAACAGTTTTAGAAGGCAGTT | wc -c
61
L= 61
'''

@mkdir('geneset.dir')
@originate("geneset.dir/tr2g.txt")
def t2g(outfile):
    '''This function will convert transcript to genes using t2g
       script'''

    PY_SRC_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__)))

    statement = '''
       zcat < %(geneset)s | %(PY_SRC_PATH)s/t2g.py  > %(outfile)s'''

    P.run(statement)


@mkdir('geneset.dir')
@originate("geneset.dir/introns.fa")
def intron_bed2fa(outfile):
    '''This converts introns bed to introns fa'''

    tmp_bed = P.get_temp_filename(".")

    statement = '''zcat < %(intron_bed)s > %(tmp_bed)s &&
    # index the fa file before next line &&
    bedtools getfasta -name -fo introns.fa -fi %(genome_file)s -bed %(tmp_bed)s'''

    P.run(statement)
    os.unlink(tmp_bed)



@transform(intron_bed2fa,
           regex("(\S+).fa"),
           r"geneset.dir/\1_transcripts.txt")
def introns_transcripts(infile, outfile):
    '''get a list of all of the intronic transcript IDs represented in our FASTA file, with version numbers.'''

    statement = '''cat %(infile)s | awk '/^>/ {print $0}' | tr "_" " " | awk '{print $3"."$4}' > %(outfile)s'''

    P.run(statement)


@transform(introns_transcripts,
           regex("geneset.dir/(\S+)_transcripts.txt"),
           r"geneset.dir/\1_transcripts_no_version.txt")
def introns_transcripts_no_version(infile, outfile):
    '''list of all of the intronic transcript IDs represented in our FASTA file, without version numbers.'''

    staement = '''cat %(infile)s | tr "." " " | awk '{print $1}' > %(outfile)s '''

    P.run(statement)


@transform(introns_transcripts,
           regex("geneset.dir/(\S+)_transcripts.txt"),
           r"geneset.dir/\1_transcripts.to_capture.txt")
def add_identifier(infile outfile):
    '''add an identifier to the transcript IDs '''

    statement = '''cat %(infile)s | awk '{print $0"."NR"-I"}' > %(outfile)s'''

    P.run(statement)


@merge([introns_transcripts,t2g],
       r"geneset.dir/introns_t2g.txt")
def map_trans2gene(infiles, outfile):
    '''map the transcripts to their respective genes.'''

    tr2g, introns_transcripts = infiles

    statement = '''awk 'NR==FNR{a[$1]=$2; b[$1]=$3;next} {$2=a[$1];$3=b[$1]} 1' %(tr2g)s %(introns_transcripts)s > %(outfile)s'''

    P.run(statement)


@merge([map_trans2gene,intron_bed2fa],
           r"geneset.dir/\introns.correct_header.fa")
def fix_intron_fasta(infiles, outfile):
    '''fix all of the headers for the introns FASTA file so that they
    contain the transcript ID, an identifier specifying that the transcript
    is an “intronic” transcript, and a unique number to avoid duplicates.'''

    introns_t2g, introns = infiles

    tmp_fasta = P.get_temp_filename(".")

    statement = '''awk '{print ">"$1"."NR"-I"" gene_id:"$2" gene_name:"$3}' %(introns_t2g)s > %(tmp_fasta)s  &&
                awk -v var=1 'FNR==NR{a[NR]=$0;next}{ if ($0~/^>/) {print a[var], var++} else {print $0}}' %(tmp_fasta)s  %(introns)s > %(outfile)s '''

    P.run(statement)
    os.unlink()


@mkdir('geneset.dir')
@originate("geneset.dir/cDNA_transcripts_no_version.txt")
def capture_list(outfile):
    '''Get the transcripts to capture list and transcripts to genes for cDNA'''

    tmp_cdna = P.get_temp_filename(".")

    statement = '''zcat < %(cdna_fasta)s cDNA.fa | awk '/^>/ {print $0}' | tr "_" " " | awk '{print $3}' > %(tmp_cdna)s  &&
                   cat %(tmp_cdna)s  | tr "." " " | awk '{print $1}' > %(outfile)s '''


    P.run(statement)
    os.unlink()


@mkdir('geneset.dir')
@originate("geneset.dir/cDNA_transcripts.to_capture.txt")
def map_tran_gene(outfile):
    ''''Add an identifier to the transcript IDs'''

    tmp_cdna = P.get_temp_filename(".")

    statement = '''zcat < %(cdna_fasta)s | awk '/^>/ {print $0}' | tr "_" " " | awk '{print $3}' > %(tmp_cdna)s &&
                   cat %(tmp_cdna)s | awk '{print $0"."NR}' > %(outfile)s'''

    P.run(statement)


@transform(t2g,
           regex("(\S+).txt"),
           r"geneset.dir/cDNA_\1.txt")
def map_tr2gene(infile, outfile):
    '''Map the transcripts to genes.'''

    tmp_cdna = P.get_temp_filename(".")

    statement = '''zcat < %(cdna_fasta)s | awk '/^>/ {print $0}' | tr "_" " " | awk '{print $3}' > %(tmp_cdna)s &&
                   awk 'NR==FNR{a[$1]=$2; b[$1]=$3;next} {$2=a[$1];$3=b[$1]} 1' %(infile)s %(tmp_cdna)s  > %(outfile)s'''


    P.run(statement)


def finx_intron_fa_header(infile, outfile):
    '''Fix the INTRONS FASTA header'''

    statement = '''awk '{print ">"$1"."NR" gene_id:"$2" gene_name:"$3}' cDNA_t2g.txt > cDNA_fasta_header.txt &&
                   awk -v var=1 'FNR==NR{a[NR]=$0;next}{ if ($0~/^>/) {print a[var], var++} else {print $0}}' cDNA_fasta_header.txt $cDNA_fa > cDNA.correct_header.fa'''

    P.run(statement)

@merge([PARAMS['geneset'], PARAMS['geneset2']],
           r"geneset.dir/geneset_all.fa")
def buildReferenceTranscriptome(infiles, outfile):
    '''
    Builds a reference transcriptome from the provided GTF geneset - generates
    a fasta file containing the sequence of each feature labelled as
    "exon" in the GTF.
    --fold-at specifies the line length in the output fasta file
    Parameters
    ----------
    infile: str
        path to the GTF file containing transcript and gene level annotations
    genome_dir: str
        :term: `PARAMS` the directory of the reference genome
    genome: str
        :term: `PARAMS` the filename of the reference genome (without .fa)
    outfile: str
        path to output file
    '''
    geneset1, geneset2 = infiles
    genome_file1 = os.path.abspath(
        os.path.join(PARAMS["genome_dir"], PARAMS["genome"] + ".fa"))

    if PARAMS['mixed_species']:
        genome_file2 = os.path.abspath(
        os.path.join(PARAMS["genome_dir2"], PARAMS["genome2"] + ".fa"))
        tmp1 = P.get_temp_filename('.')
        tmp2 = P.get_temp_filename('.')
        statement = '''
                       zcat %(geneset1)s |
                       awk '$3=="exon"'|
                       cgat gff2fasta
                       --is-gtf
                       --genome-file=%(genome_file1)s
                       --fold-at=60 -v 0
                       --log=%(outfile)s.log > %(tmp1)s &&
                       zcat %(geneset2)s |
                       awk '$3=="exon"'|
                       cgat gff2fasta
                       --is-gtf
                       --genome-file=%(genome_file2)s
                       --fold-at=60 -v 0
                       --log=%(outfile)s.log > %(tmp2)s &&
                       cat %(tmp1)s %(tmp2)s > %(outfile)s &&
                       samtools faidx %(outfile)s
                       '''
    else:
        statement = '''
                       zcat %(geneset1)s |
                       awk '$3=="exon"'|
                       cgat gff2fasta
                       --is-gtf
                       --genome-file=%(genome_file1)s
                       --fold-at=60 -v 0
                       --log=%(outfile)s.log > %(outfile)s &&
                       samtools faidx %(outfile)s
                       '''

    P.run(statement)
    if PARAMS['mixed_species']:
        os.unlink(tmp1)
        os.unlink(tmp2)

@active_if(PARAMS['salmon_alevin'])
@transform(buildReferenceTranscriptome,
           suffix(".fa"),
           ".salmon.index")
def buildSalmonIndex(infile, outfile):
    '''
    Builds a salmon index for the reference transriptome
    Parameters
    ----------
    infile: str
       path to reference transcriptome - fasta file containing transcript
       sequences
    salmon_kmer: int
       :term: `PARAMS` kmer size for sailfish.  Default is 31.
       Salmon will ignores transcripts shorter than this.
    salmon_index_options: str
       :term: `PARAMS` string to append to the salmon index command to
       provide specific options e.g. --force --threads N
    outfile: str
       path to output file
    '''

    job_memory = "12G"

    statement = '''
    rm -rf %(outfile)s;
    salmon index -k %(salmon_kmer)i %(salmon_index_options)s -t %(infile)s -i %(outfile)s
    '''

    P.run(statement)

@active_if(PARAMS['kallisto_bustools'])
@transform(buildReferenceTranscriptome,
           suffix(".fa"),
           ".kallisto.index")
def buildKallistoIndex(infile, outfile):
    '''
    Builds a kallisto index for the reference transcriptome
    Parameters
    ----------
    infile: str
       path to reference transcriptome - fasta file containing transcript
       sequences
    kallisto_kmer: int
       :term: `PARAMS` kmer size for Kallisto.  Default is 31.
       Kallisto will ignores transcripts shorter than this.
    outfile: str
       path to output file
    '''

    job_memory = "12G"

    statement = '''
    kallisto index -i %(outfile)s -k %(kallisto_kmer)s %(infile)s
    '''

    P.run(statement)


@originate("transcript2geneMap.tsv")
def getTranscript2GeneMap(outfile):
    ''' Extract a 1:1 map of transcript_id to gene_id from the geneset '''

    iterator = GTF.iterator(iotools.open_file(PARAMS['geneset']))
    transcript2gene_dict = {}

    for entry in iterator:

        # Check the same transcript_id is not mapped to multiple gene_ids!
        if entry.transcript_id in transcript2gene_dict:
            if not entry.gene_id == transcript2gene_dict[entry.transcript_id]:
                raise ValueError('''multipe gene_ids associated with
                the same transcript_id %s %s''' % (
                    entry.gene_id,
                    transcript2gene_dict[entry.transcript_id]))
        else:
            transcript2gene_dict[entry.transcript_id] = entry.gene_id

    if PARAMS['mixed_species']:
        iterator = GTF.iterator(iotools.open_file(PARAMS['geneset2']))

        for entry in iterator:

            # Check the same transcript_id is not mapped to multiple gene_ids!
            if entry.transcript_id in transcript2gene_dict:
                if not entry.gene_id == transcript2gene_dict[entry.transcript_id]:
                    raise ValueError('''multipe gene_ids associated with
                                     the same transcript_id %s %s''' % (
                            entry.gene_id,
                            transcript2gene_dict[entry.transcript_id]))
            else:
                transcript2gene_dict[entry.transcript_id] = entry.gene_id

    with iotools.open_file(outfile, "w") as outf:
        outf.write("transcript_id\tgene_id\n")
        for key, value in sorted(transcript2gene_dict.items()):
            outf.write("%s\t%s\n" % (key, value))

############################################
# Perform read quality steps
############################################


@follows(mkdir("fastqc_pre.dir"))
@transform(SEQUENCEFILES,
           regex("(\S+).fastq.(\d).gz"),
           r"fastqc_pre.dir/\1.fastq.\2_fastqc.html")
def runFastQC(infile, outfile):
    '''
    Fastqc is ran to determine the quality of the reads from the sequencer
    '''
    # paired end mode
    if "fastq.1.gz" in infile:
        second_read = infile.replace(".fastq.1.gz", ".fastq.2.gz")
        statement = "fastqc -q -o fastqc_pre.dir/ %(infile)s %(second_read)s"

    else:
        statement = "fastqc -q -o fastqc_pre.dir/ %(infile)s"

    P.run(statement)


############################################
# Pseudoalignment
############################################

if "merge_pattern_input" in PARAMS and PARAMS["merge_pattern_input"]:
    SEQUENCEFILES_REGEX = regex(
        r"%s/%s.(fastq.gz|fastq.1.gz)" % (
            DATADIR, PARAMS["merge_pattern_input"].strip()))

    SEQUENCEFILES_KALLISTO_OUTPUT = (
        r"kallisto.dir/%s/bus/output.bus" % (
            PARAMS["merge_pattern_output"].strip()))

    SEQUENCEFILES_SALMON_OUTPUT = (
        r"salmon.dir/%s/alevin/quants_mat.gz" % (
            PARAMS["merge_pattern_output"].strip()))

else:
    SEQUENCEFILES_REGEX = regex(
        "(\S+).(fastq.gz|fastq.1.gz)")

    SEQUENCEFILES_KALLISTO_OUTPUT = (
        r"kallisto.dir/\1/bus/output.bus")

    SEQUENCEFILES_SALMON_OUTPUT = (
        r"salmon.dir/\1/alevin/quants_mat.gz")

#############################
# Salmon- Alevin
#############################

@active_if(PARAMS['salmon_alevin'])
@follows(mkdir("salmon.dir"))
@collate(SEQUENCEFILES,
         SEQUENCEFILES_REGEX,
         add_inputs(buildSalmonIndex, getTranscript2GeneMap),
         SEQUENCEFILES_SALMON_OUTPUT)
def runSalmonAlevin(infiles, outfile):
    '''
    Alevin is integrated with salmon to quantify and analyse 3' tagged-end
    single-cell sequencing data. Alevin supports 10Xv1, 10Xv2 and Drop-Seq
    sc technology.
    '''

    aligner = 'salmon_alevin'
    infiles = ModuleSC.check_multiple_read_files(infiles)
    fastqfile, index, t2gmap = infiles
    fastqfiles = ModuleSC.check_paired_end(fastqfile, aligner)
    if isinstance(fastqfiles, list):
        CB_UMI_fastq = " ".join(fastqfiles[0])
        reads_fastq = " ".join(fastqfiles[1])

    outfolder = outfile.rsplit('/',2)[0]

    salmon_options = PARAMS['salmon_run_options']

    statement = '''
    salmon alevin -l %(salmon_librarytype)s -1 %(CB_UMI_fastq)s -2  %(reads_fastq)s
    --%(salmon_sctechnology)s -i %(index)s -p %(salmon_threads)s -o %(outfolder)s
    --tgMap %(t2gmap)s --dumpFeatures --dumpUmiGraph %(salmon_options)s
    '''

    job_memory = PARAMS["salmon_job_memory"]
    job_threads = PARAMS['salmon_threads']
    P.run(statement)

#############################
# Kallisto- Bus
#############################

@active_if(PARAMS['kallisto_bustools'])
@follows(mkdir("kallisto.dir"))
@collate(SEQUENCEFILES,
         SEQUENCEFILES_REGEX,
         add_inputs(buildKallistoIndex, getTranscript2GeneMap),
         SEQUENCEFILES_KALLISTO_OUTPUT)
def runKallistoBus(infiles, outfile):
    '''
    Generates BUS files for single-cell sequencing

    infiles: raw sequencing fastq files, kallisto index

    '''
    aligner = 'kallisto_bus'
    infiles = ModuleSC.check_multiple_read_files(infiles)
    fastqfile, index, t2gmap = infiles
    fastqfiles = ModuleSC.check_paired_end(fastqfile, aligner)
    fastqfiles = " ".join(fastqfiles)

    outfolder = outfile.rsplit('/',1)[0]

    statement = '''
    kallisto bus -i %(index)s -o %(outfolder)s -x %(kallisto_sctechnology)s
    -t %(kallisto_threads)s %(fastqfiles)s
    '''

    job_memory = '20G'

    P.run(statement)

#########################
# Process bus file
#########################

# Must have bustools installed
#https://github.com/BUStools/bustools

@active_if(PARAMS['kallisto_bustools'])
@transform(runKallistoBus,
           regex("kallisto.dir/(\S+)/bus/output.bus"),
           r"kallisto.dir/\1/bus/output.bus.sorted.txt")
def busText(infile, outfile):
    '''
    Sort the bus file produced by kallisto and then convert it to a text file.
    '''

    tmp_bus  = P.get_temp_filename(".")
    job_memory = '10G'

    job_memory = '10G'

    statement = '''
    bustools sort -o %(tmp_bus)s %(infile)s ;
    bustools text -o %(outfile)s %(tmp_bus)s
    '''

    P.run(statement)



@transform(busText,
           suffix(".sorted.txt"),
           add_inputs(getTranscript2GeneMap),
           r"\1_GCcoordmatrix.mtx")
def busCount(infiles, outfile):
    '''
    Takes the sorted BUS file, corresponding ec matrix and transcript text file and generates a count matrix and tag count comparison??
    '''

    sorted_bus, t2gmap = infiles
    folder = sorted_bus.rsplit('/', 1)[0]
    ROOT = os.path.dirname(__file__)
    bus2count = ROOT + "/bus2count.py"
    exp_cells = PARAMS['kallisto_expectedcells']
    threads = PARAMS['kallisto_threads']

    statement = '''
    rm -rf %(folder)s/bus_count.log;
    python %(bus2count)s --dir %(folder)s --t2gmap %(t2gmap)s --expectedcells %(exp_cells)s --threads %(threads)s -o %(outfile)s
    '''

    job_memory = "30G"

    P.run(statement)

#########################
# SCE object
#########################

@follows(mkdir("SCE.dir"))
@active_if(PARAMS['salmon_alevin'])
@transform(runSalmonAlevin,
           regex(r"salmon.dir/(.*)/alevin/quants_mat.gz"),
           r"SCE.dir/\1/alevin/sce.rds")
def readAlevinSCE(infile,outfile):
    '''
    Collates alevin count matrices for each sample
    Creates a single cell experiment class in R and saves as an r object
    '''
    working_dir = os.getcwd()
    R_ROOT = os.path.join(os.path.dirname(__file__), "R")
    species = PARAMS['sce_species']
    gene_name = PARAMS['sce_genesymbol']
    pseudo = 'alevin'
    if PARAMS['downsample_active']:
        downsample = "-d" + PARAMS['downsample_to']
    else:
        downsample = ""

    job_memory = "40G"

    statement = '''
    Rscript %(R_ROOT)s/sce.R -w %(working_dir)s -i %(infile)s -o %(outfile)s --species %(species)s --genesymbol %(gene_name)s --pseudoaligner %(pseudo)s %(downsample)s
    '''

    P.run(statement)


## Kallisto SCE object
@follows(mkdir("SCE.dir"))
@active_if(PARAMS['kallisto_bustools'])
@transform(busCount,
           regex("kallisto.dir/(\S+)/bus/output.bus_GCcoordmatrix.mtx"),
           r"SCE.dir/\1/bus/sce.rds")
def readBusSCE(infile, outfile):
    '''
    Takes in gene count matrices for each sample
    Creates a single cell experiment class in R and saves as an r object
    '''

    working_dir = os.getcwd()
    R_ROOT = os.path.join(os.path.dirname(__file__), "R")
    species = PARAMS['sce_species']
    gene_name = PARAMS['sce_genesymbol']
    pseudo = 'kallisto'

    job_memory = "10G"

    statement = '''
    Rscript %(R_ROOT)s/sce.R -w %(working_dir)s -i %(infile)s -o %(outfile)s --species %(species)s --genesymbol %(gene_name)s --pseudoaligner %(pseudo)s
    '''

    P.run(statement)

## Kallisto SCE object using BUSpaRse R package and emptydrops (DropletUtils function)
@follows(mkdir("SCE.dir"))
@follows(busText)
@active_if(PARAMS['kallisto_bustools'])
@transform(busText,
           regex("kallisto.dir/(\S+)/bus/output.bus.sorted.txt"),
           add_inputs(PARAMS['geneset'], getTranscript2GeneMap),
           r"SCE.dir/\1/bus/sce.rds")
def BUSpaRse(infiles, outfile):
    '''
    Create kallisto SCE object. Use BUSpaRse package to read in bus file and convert to TCC and gene counts matrix.
    Create knee plot and use point of inflection to estimate number of empty droplets and cells.
    Or use emptyDrops function from DropletUtils package to compare to the ambient profile.
    '''

    bus_text, gtf, t2gmap = infiles
    R_ROOT = os.path.join(os.path.dirname(__file__),"pipeline_singlecell","R")
    est_cells = PARAMS['kallisto_expectedcells']

    job_memory = '50G'

    statement = '''
    Rscript %(R_ROOT)s/BUSPaRse.R -i %(bus_text)s -o %(outfile)s --estcells %(est_cells)s --t2g %(t2gmap)s -g %(gtf)s
    '''

    P.run(statement)


@transform((BUSpaRse, readAlevinSCE),
           regex(r"SCE.dir/(\S+)/(\S+)/(\S+).rds"),
           r"SCE.dir/\1/\2/sce.rds")
def combine_alevin_bus(infiles, outfiles):
    '''
    dummy task to combine alevin and bus output into one task
    '''

#########################
# Multiqc
#########################

@follows(mkdir("MultiQC_report.dir"))
@follows(runFastQC)
@originate("MultiQC_report.dir/multiqc_report.html")
def build_multiqc(infile):
    '''build mulitqc report'''

    statement = (
        "export LANG=en_GB.UTF-8 && "
        "export LC_ALL=en_GB.UTF-8 && "
        "multiqc . -f && "
        "mv multiqc_report.html MultiQC_report.dir/")

    P.run(statement)

#########################
# QC step - needs some work so have commented out at the moment
#########################

#@follows(build_multiqc)
#@follows(mkdir("QC_report.dir"))
#@transform(combine_alevin_bus,
#           regex("(\S+/\S+/\S+)/sce.rds"),
#           r"\1/pass.rds")
#def run_qc(infile, outfile):
#    """
#    Runs an Rmarkdown report that allows users to visualise and set their
#    quality parameters according to the data. The aim is for the pipeline
#    to generate default thresholds then the user can open the Rmarkdown in
#    rstudio and re-run the report, modifying parameters changes to suit the
#    data
#    """
#
#    inf_dir = os.path.dirname(infile)
#    NOTEBOOK_ROOT = os.path.join(os.path.dirname(__file__), "Rmarkdown")
#
#    job_memory = 'unlimited'
#
#    statement = '''cp %(NOTEBOOK_ROOT)s/Sample_QC.Rmd %(inf_dir)s &&
#                   R -e "rmarkdown::render('%(inf_dir)s/Sample_QC.Rmd',encoding = 'UTF-8')"'''
#
#    P.run(statement)

@follows(combine_alevin_bus, build_multiqc)
def full():
    pass

def main(argv=None):
    if argv is None:
        argv = sys.argv
    P.main(argv)

if __name__ == "__main__":
    sys.exit(P.main(sys.argv))
