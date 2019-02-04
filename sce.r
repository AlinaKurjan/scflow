#!/usr/bin/env Rscript

library("tidyverse")
library("SingleCellExperiment")
library("optparse")

option_list = list(
  make_option(c("-w", "--workingdir"), type="character", default=NULL, 
              help="working directory", metavar="character"),
  make_option(c("-i", "--input"), type="character", default="sce.rds", 
              help="input file (quants_mat.gz path) [default= %default]", metavar="character"),
  make_option(c("-o", "--out"), type="character", default="sce.rds", 
              help="output file name [default= %default]", metavar="character")
); 

opt_parser = OptionParser(option_list=option_list);
opt = parse_args(opt_parser);

wd = opt$workingdir

setwd(wd)
out <- opt$out
input <- opt$input
sample_folders = list.files(path = "wd/salmon.dir/")

readAlevin <- function(files) {
  dir <- sub("/alevin$","",dirname(files))
  barcode.file <- file.path(dir, "alevin/quants_mat_rows.txt")
  gene.file <- file.path(dir, "alevin/quants_mat_cols.txt")
  matrix.file <- file.path(dir, "alevin/quants_mat.gz")
  var.file <- file.path(dir, "alevin/quants_var_mat.gz")
  for (f in c(barcode.file, gene.file, matrix.file)) {
    if (!file.exists(f)) {
      stop("expecting 'files' to point to 'quants_mat.gz' file in a directory 'alevin'
           also containing 'quants_mat_rows.txt' and 'quant_mat_cols.txt'.
           please re-run alevin preserving output structure")
    }
    }
  cell.names <- readLines(barcode.file)
  gene.names <- readLines(gene.file)
  num.cells <- length(cell.names)
  num.genes <- length(gene.names)
  mat <- matrix(nrow=num.genes, ncol=num.cells, dimnames=list(gene.names, cell.names))
  con <- gzcon(file(matrix.file, "rb"))
  for (j in seq_len(num.cells)) {
    mat[,j] <- readBin(con, double(), endian = "little", n=num.genes)
  }
  close(con)
  # if inferential replicate variance exists:
  if (file.exists(var.file)) {
    counts.mat <- mat
    var.mat <- mat
    con <- gzcon(file(var.file, "rb"))
    for (j in seq_len(num.cells)) {
      var.mat[,j] <- readBin(con, double(), endian = "little", n=num.genes)
    }
    close(con)
    mat <- list(counts.mat, var.mat)
  }
  return(mat)
    }

mat <- readAlevin(input)
v <- log2(mat + 1)
sce <- SingleCellExperiment(assays = list(counts = mat, logcounts = v))
saveRDS(object = sce, file = out)



