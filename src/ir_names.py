"""The names the code generator gives things in the module, in one place.

The generator uses them to name what it builds; the semantic pass uses them to refuse an
extern function whose C name one of them already holds -- a declare under that name would
be a second definition of it, or would bind the call to the compiler's own function.
"""

FN_PREFIX = "fn_"                 # fn main := ... becomes @fn_main, not a second @main

# What every module holds before the first function of the program: main, the two C
# functions behind print, exit and read(), and the global strings exit and print use.
# Other strings get their names (str, str.1, ...) only after the externs are declared,
# so they step around an extern's name by themselves.
MODULE_NAMES = ("main", "printf", "scanf", "fmt_int", "fmt_str", "word_true", "word_false")


def fn_name(name):
    """The IR name of the program's function `name`."""
    return FN_PREFIX + name


def member_name(struct_name, method_name):
    """The IR name of a member function, before the generator makes it unique: Point_d2."""
    return f"{struct_name}_{method_name}"
