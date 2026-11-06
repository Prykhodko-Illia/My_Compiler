// tests/ffi/geometry.c -- the body of extern fn isqrt in geometry.txt
int isqrt(int v)
{
    int r = 0;
    while ((r + 1) * (r + 1) <= v)
        r = r + 1;
    return r;
}
