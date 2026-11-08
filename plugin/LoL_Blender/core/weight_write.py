"""Write vertex weights without the RNA clamp at 1.

Blender's C code (`vgroup_nr_vert_add`) keeps heat weights above 1, but the Python APIs clamp.
A weight above 1 is written straight into the entry's `MDeformWeight` via `as_pointer()`.
"""

import ctypes

#: Offset of `MDeformWeight.weight`, after the 4-byte `def_nr`.
_WEIGHT_OFFSET = ctypes.sizeof(ctypes.c_uint)


def replace_weight(group, vertices, weight):
    """`group.add(vertices, weight, 'REPLACE')` without the RNA clamp at 1."""
    group.add(vertices, weight, 'REPLACE')
    if not weight > 1.0:
        return
    mesh = group.id_data.data
    index = group.index
    for vertex in vertices:
        for element in mesh.vertices[vertex].groups:
            if element.group == index:
                ctypes.c_float.from_address(element.as_pointer() + _WEIGHT_OFFSET).value = weight
                break


def raise_weights(obj, over):
    """Write the (vertex, group index, weight > 1) entries a clamping API stored as 1."""
    mesh = obj.data
    for vertex, index, weight in over:
        for element in mesh.vertices[vertex].groups:
            if element.group == index:
                ctypes.c_float.from_address(element.as_pointer() + _WEIGHT_OFFSET).value = weight
                break


class DeformEdit:
    """Bulk weight edits through BMesh, about 3x faster than `VertexGroup.add`.

    Same entry order as the RNA calls. Weights above 1 are restored on close. Only for temporary
    objects: closing rewrites the whole mesh datablock.
    """

    def __init__(self, obj):
        import bmesh
        self.obj = obj
        self.bm = bmesh.new()
        self.bm.from_mesh(obj.data)
        self.bm.verts.ensure_lookup_table()
        self.layer = self.bm.verts.layers.deform.verify()
        self.over = []

    def entries(self, vertex):
        return list(self.bm.verts[vertex][self.layer].items())

    def set(self, vertex, index, weight):
        self.bm.verts[vertex][self.layer][index] = weight
        if weight > 1.0:
            self.over.append((vertex, index, weight))

    def remove(self, vertex, index):
        deform = self.bm.verts[vertex][self.layer]
        if index in deform:
            del deform[index]

    def close(self):
        self.bm.to_mesh(self.obj.data)
        self.bm.free()
        self.obj.data.update()
        raise_weights(self.obj, self.over)
