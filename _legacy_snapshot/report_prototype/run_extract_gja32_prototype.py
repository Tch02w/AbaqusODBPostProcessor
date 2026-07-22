import builtins
import abaqusConstants


# Abaqus exports a symbol named ``sum`` through abaqusConstants, which shadows
# Python's numeric built-in when the extraction script imports all constants.
abaqusConstants.sum = builtins.sum

execfile(
    "abaqus_odb_prototype/extract_gja32_prototype.py",
    globals(),
)
