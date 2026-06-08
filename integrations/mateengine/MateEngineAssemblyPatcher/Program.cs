using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Mono.Cecil;
using Mono.Cecil.Cil;

const string DefaultAssemblyPath = @"D:\Program Files (x86)\Steam\steamapps\common\MateEngine\MateEngineX_Data\Managed\Assembly-CSharp.dll";
const string TextPrefix = "NATSUME_TEXT::";
const string MouthEventType = "natsume_mouth";
const string FormEventType = "natsume_form";
const string FreezeEventType = "natsume_freeze";
const string ActionEventType = "natsume_action";
const string RuntimeTypeName = "NatsumeMateEngineRuntime";
const string ApplyMouthMethodName = "ApplyMouth";
const string ApplyFormMethodName = "ApplyForm";
const string ApplyFormFrameMethodName = "ApplyFormFrame";
const string ApplyFormInputsMethodName = "ApplyFormInputs";
const string ApplyFreezeMethodName = "ApplyFreeze";
const string ApplyFreezeFrameMethodName = "ApplyFreezeFrame";
const string ApplyActionMethodName = "ApplyAction";
const string CurrentMouthFieldName = "CurrentMouthWeight";
const string CurrentFormNameFieldName = "CurrentFormBlendshapeName";
const string CurrentFormWeightFieldName = "CurrentFormWeight";
const string FreezeMotionFieldName = "FreezeMotionEnabled";

var assemblyPath = args.Length > 0 ? args[0] : DefaultAssemblyPath;
if (!File.Exists(assemblyPath))
{
    Console.Error.WriteLine($"Assembly not found: {assemblyPath}");
    return 2;
}

var backupPath = assemblyPath + ".natsume-bak";
if (!File.Exists(backupPath))
{
    File.Copy(assemblyPath, backupPath);
}

var readerParameters = new ReaderParameters
{
    ReadSymbols = false,
    InMemory = true,
};
var module = ModuleDefinition.ReadModule(assemblyPath, readerParameters);

var changed = false;
changed |= PatchFreeTextBubble(module);
var applyMouth = EnsureRuntimeApplyMouth(module, ref changed);
var applyForm = EnsureRuntimeApplyForm(module, ref changed, out var applyFormFrame);
var applyFormInputs = EnsureRuntimeApplyFormInputs(module, ref changed);
var applyFreeze = EnsureRuntimeApplyFreeze(module, ref changed);
var applyFreezeFrame = EnsureRuntimeApplyFreezeFrame(module, ref changed);
var applyAction = EnsureRuntimeApplyAction(module, ref changed);
changed |= PatchUniversalBlendshapesLateUpdate(module, applyFormFrame, applyFormInputs, applyFreezeFrame);
changed |= PatchMouthEvent(module, applyMouth);
changed |= PatchFormEvent(module, applyForm);
changed |= PatchFreezeEvent(module, applyFreeze);
changed |= PatchActionEvent(module, applyAction);

if (!changed)
{
    Console.WriteLine("MateEngine Assembly-CSharp.dll is already patched for Natsume text, mouth sync, form sync, freeze sync, and native action sync.");
    module.Dispose();
    return 0;
}

var tempPath = assemblyPath + ".natsume-tmp";
module.Write(tempPath);
module.Dispose();
File.Copy(tempPath, assemblyPath, overwrite: true);
File.Delete(tempPath);

Console.WriteLine($"Patched MateEngine Assembly-CSharp.dll. Backup: {backupPath}");
return 0;

static bool PatchFreeTextBubble(ModuleDefinition module)
{
    var targetType = FindType(module, "AvatarMinecraftMessages");
    if (targetType == null)
    {
        Console.Error.WriteLine("Type AvatarMinecraftMessages not found.");
        Environment.ExitCode = 3;
        return false;
    }

    var method = targetType.Methods.FirstOrDefault(candidate =>
        candidate.Name == "InjectEntity"
        && candidate.Parameters.Count == 2
        && candidate.ReturnType.FullName == module.TypeSystem.String.FullName
    );
    if (method == null)
    {
        Console.Error.WriteLine("Method AvatarMinecraftMessages.InjectEntity(string,string) not found.");
        Environment.ExitCode = 4;
        return false;
    }

    if (method.Body.Instructions.Any(instruction => instruction.OpCode == OpCodes.Ldstr && Equals(instruction.Operand, TextPrefix)))
    {
        return false;
    }

    var stringType = module.TypeSystem.String.Resolve();
    var startsWith = stringType.Methods.First(candidate =>
        candidate.Name == "StartsWith"
        && candidate.Parameters.Count == 1
        && candidate.Parameters[0].ParameterType.FullName == module.TypeSystem.String.FullName
    );
    var substring = stringType.Methods.First(candidate =>
        candidate.Name == "Substring"
        && candidate.Parameters.Count == 1
        && candidate.Parameters[0].ParameterType.FullName == module.TypeSystem.Int32.FullName
    );

    var il = method.Body.GetILProcessor();
    var first = method.Body.Instructions.First();
    var continueInstruction = il.Create(OpCodes.Nop);
    var injected = new[]
    {
        il.Create(OpCodes.Ldarg_2),
        il.Create(OpCodes.Brfalse, continueInstruction),
        il.Create(OpCodes.Ldarg_2),
        il.Create(OpCodes.Ldstr, TextPrefix),
        il.Create(OpCodes.Callvirt, module.ImportReference(startsWith)),
        il.Create(OpCodes.Brfalse, continueInstruction),
        il.Create(OpCodes.Ldarg_2),
        il.Create(OpCodes.Ldc_I4, TextPrefix.Length),
        il.Create(OpCodes.Callvirt, module.ImportReference(substring)),
        il.Create(OpCodes.Ret),
        continueInstruction,
    };

    foreach (var instruction in injected)
    {
        il.InsertBefore(first, instruction);
    }
    return true;
}

static MethodDefinition EnsureRuntimeApplyMouth(ModuleDefinition module, ref bool changed)
{
    var runtimeType = FindType(module, RuntimeTypeName);
    if (runtimeType == null)
    {
        runtimeType = new TypeDefinition(
            "",
            RuntimeTypeName,
            TypeAttributes.NotPublic
            | TypeAttributes.Abstract
            | TypeAttributes.Sealed
            | TypeAttributes.BeforeFieldInit,
            module.TypeSystem.Object
        );
        module.Types.Add(runtimeType);
        changed = true;
    }
    var currentMouthField = runtimeType.Fields.FirstOrDefault(field =>
        field.Name == CurrentMouthFieldName
        && field.FieldType.FullName == module.TypeSystem.Single.FullName
    );
    if (currentMouthField == null)
    {
        currentMouthField = new FieldDefinition(
            CurrentMouthFieldName,
            FieldAttributes.Public | FieldAttributes.Static,
            module.TypeSystem.Single
        );
        runtimeType.Fields.Add(currentMouthField);
        changed = true;
    }
    else if ((currentMouthField.Attributes & FieldAttributes.FieldAccessMask) != FieldAttributes.Public)
    {
        currentMouthField.Attributes &= ~FieldAttributes.FieldAccessMask;
        currentMouthField.Attributes |= FieldAttributes.Public | FieldAttributes.Static;
        changed = true;
    }

    var universalBlendshapes = FindType(module, "UniversalBlendshapes")
        ?? throw new InvalidOperationException("Type UniversalBlendshapes not found.");
    var findObjectsTemplate = FindGenericMethodTemplate(module, "FindObjectsOfTypeAll", parameterCount: 0)
        ?? throw new InvalidOperationException("UnityEngine.Resources.FindObjectsOfTypeAll<T>() not found.");
    var clamp01 = FindMethodReference(module, method => method.FullName == "System.Single UnityEngine.Mathf::Clamp01(System.Single)")
        ?? throw new InvalidOperationException("UnityEngine.Mathf.Clamp01(float) not found.");
    var objectInequality = FindMethodReference(module, method => method.FullName == "System.Boolean UnityEngine.Object::op_Inequality(UnityEngine.Object,UnityEngine.Object)")
        ?? throw new InvalidOperationException("UnityEngine.Object.op_Inequality not found.");
    var skinnedFindGeneric = EnumerateMethodReferences(module)
        .OfType<GenericInstanceMethod>()
        .First(method =>
            method.ElementMethod.Name == "FindObjectsOfType"
            && method.ElementMethod.Parameters.Count == 1
            && method.GenericArguments.Count == 1
            && method.GenericArguments[0].FullName == "UnityEngine.SkinnedMeshRenderer"
        );
    var skinnedRendererType = skinnedFindGeneric.GenericArguments[0];
    var findSkinnedTemplate = skinnedFindGeneric.ElementMethod;
    var getSharedMesh = FindMethodReference(module, method => method.FullName == "UnityEngine.Mesh UnityEngine.SkinnedMeshRenderer::get_sharedMesh()")
        ?? throw new InvalidOperationException("SkinnedMeshRenderer.sharedMesh getter not found.");
    var setRendererEnabled = FindMethodReference(module, method => method.FullName == "System.Void UnityEngine.Renderer::set_enabled(System.Boolean)")
        ?? throw new InvalidOperationException("Renderer.enabled setter not found.");
    var getBlendShapeCount = FindMethodReference(module, method => method.FullName == "System.Int32 UnityEngine.Mesh::get_blendShapeCount()")
        ?? throw new InvalidOperationException("Mesh.blendShapeCount getter not found.");
    var getBlendShapeName = FindMethodReference(module, method => method.FullName == "System.String UnityEngine.Mesh::GetBlendShapeName(System.Int32)")
        ?? throw new InvalidOperationException("Mesh.GetBlendShapeName(int) not found.");
    var setBlendShapeWeight = FindMethodReference(module, method => method.FullName == "System.Void UnityEngine.SkinnedMeshRenderer::SetBlendShapeWeight(System.Int32,System.Single)")
        ?? throw new InvalidOperationException("SkinnedMeshRenderer.SetBlendShapeWeight(int,float) not found.");
    var isOpenMouthBlendshape = EnsureStringMatchMethod(
        module,
        runtimeType,
        "IsOpenMouthBlendshape",
        new[]
        {
            "A",
            "Face:A",
            "O",
            "Face:O",
            "aa",
            "Face:aa",
            "VRC_v_aa",
            "Face:VRC_v_aa",
            "VRC_v_oh",
            "Face:VRC_v_oh",
            "VRC_v_ou",
            "Face:VRC_v_ou",
            "MouthOpen",
            "ParamMouthOpenY",
            "VoiceA",
        },
        ref changed
    );
    var mouthFields = new[]
    {
        universalBlendshapes.Fields.First(field => field.Name == "A"),
        universalBlendshapes.Fields.First(field => field.Name == "I"),
        universalBlendshapes.Fields.First(field => field.Name == "U"),
        universalBlendshapes.Fields.First(field => field.Name == "E"),
        universalBlendshapes.Fields.First(field => field.Name == "O"),
    };

    var method = runtimeType.Methods.FirstOrDefault(candidate =>
        candidate.Name == ApplyMouthMethodName
        && candidate.Parameters.Count == 1
        && candidate.Parameters[0].ParameterType.FullName == module.TypeSystem.Single.FullName
    );
    if (method == null)
    {
        method = new MethodDefinition(
            ApplyMouthMethodName,
            MethodAttributes.Public | MethodAttributes.Static | MethodAttributes.HideBySig,
            module.TypeSystem.Void
        );
        method.Parameters.Add(new ParameterDefinition("value", ParameterAttributes.None, module.TypeSystem.Single));
        runtimeType.Methods.Add(method);
    }
    else
    {
        method.Body = new MethodBody(method);
    }

    method.Body.InitLocals = true;
    var arrayLocal = new VariableDefinition(new ArrayType(universalBlendshapes));
    var indexLocal = new VariableDefinition(module.TypeSystem.Int32);
    var blendLocal = new VariableDefinition(universalBlendshapes);
    var valueLocal = new VariableDefinition(module.TypeSystem.Single);
    var weightLocal = new VariableDefinition(module.TypeSystem.Single);
    var skinnedArrayLocal = new VariableDefinition(new ArrayType(skinnedRendererType));
    var skinnedIndexLocal = new VariableDefinition(module.TypeSystem.Int32);
    var skinnedLocal = new VariableDefinition(skinnedRendererType);
    var meshLocal = new VariableDefinition(getSharedMesh.ReturnType);
    var shapeIndexLocal = new VariableDefinition(module.TypeSystem.Int32);
    var shapeNameLocal = new VariableDefinition(module.TypeSystem.String);
    method.Body.Variables.Add(arrayLocal);
    method.Body.Variables.Add(indexLocal);
    method.Body.Variables.Add(blendLocal);
    method.Body.Variables.Add(valueLocal);
    method.Body.Variables.Add(weightLocal);
    method.Body.Variables.Add(skinnedArrayLocal);
    method.Body.Variables.Add(skinnedIndexLocal);
    method.Body.Variables.Add(skinnedLocal);
    method.Body.Variables.Add(meshLocal);
    method.Body.Variables.Add(shapeIndexLocal);
    method.Body.Variables.Add(shapeNameLocal);

    var findUniversal = new GenericInstanceMethod(module.ImportReference(findObjectsTemplate));
    findUniversal.GenericArguments.Add(universalBlendshapes);
    var findSkinned = new GenericInstanceMethod(module.ImportReference(findSkinnedTemplate));
    findSkinned.GenericArguments.Add(skinnedRendererType);

    var il = method.Body.GetILProcessor();
    var loopStart = il.Create(OpCodes.Nop);
    var loopContinue = il.Create(OpCodes.Nop);
    var loopCheck = il.Create(OpCodes.Nop);
    il.Append(il.Create(OpCodes.Call, findUniversal));
    il.Append(il.Create(OpCodes.Stloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, module.ImportReference(clamp01)));
    il.Append(il.Create(OpCodes.Stloc, valueLocal));
    il.Append(il.Create(OpCodes.Ldloc, valueLocal));
    il.Append(il.Create(OpCodes.Ldc_R4, 100.0f));
    il.Append(il.Create(OpCodes.Mul));
    il.Append(il.Create(OpCodes.Stloc, weightLocal));
    il.Append(il.Create(OpCodes.Ldloc, weightLocal));
    il.Append(il.Create(OpCodes.Stsfld, currentMouthField));
    il.Append(il.Create(OpCodes.Ldc_I4_0));
    il.Append(il.Create(OpCodes.Stloc, indexLocal));
    il.Append(il.Create(OpCodes.Br, loopCheck));

    il.Append(loopStart);
    il.Append(il.Create(OpCodes.Ldloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldelem_Ref));
    il.Append(il.Create(OpCodes.Stloc, blendLocal));
    il.Append(il.Create(OpCodes.Ldloc, blendLocal));
    il.Append(il.Create(OpCodes.Brfalse, loopContinue));

    for (var i = 0; i < mouthFields.Length; i++)
    {
        var mouthValueInstruction = i == 0 || i == 4 ? il.Create(OpCodes.Ldloc, weightLocal) : il.Create(OpCodes.Ldc_R4, 0.0f);
        il.Append(il.Create(OpCodes.Ldloc, blendLocal));
        il.Append(mouthValueInstruction);
        il.Append(il.Create(OpCodes.Stfld, mouthFields[i]));
    }
    il.Append(loopContinue);
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Add));
    il.Append(il.Create(OpCodes.Stloc, indexLocal));
    il.Append(loopCheck);
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldlen));
    il.Append(il.Create(OpCodes.Conv_I4));
    il.Append(il.Create(OpCodes.Blt, loopStart));

    var rendererLoopStart = il.Create(OpCodes.Nop);
    var rendererLoopContinue = il.Create(OpCodes.Nop);
    var rendererLoopCheck = il.Create(OpCodes.Nop);
    var shapeLoopStart = il.Create(OpCodes.Nop);
    var shapeLoopContinue = il.Create(OpCodes.Nop);
    var shapeLoopCheck = il.Create(OpCodes.Nop);
    var skipRendererShapes = il.Create(OpCodes.Nop);

    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Call, findSkinned));
    il.Append(il.Create(OpCodes.Stloc, skinnedArrayLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_0));
    il.Append(il.Create(OpCodes.Stloc, skinnedIndexLocal));
    il.Append(il.Create(OpCodes.Br, rendererLoopCheck));

    il.Append(rendererLoopStart);
    il.Append(il.Create(OpCodes.Ldloc, skinnedArrayLocal));
    il.Append(il.Create(OpCodes.Ldloc, skinnedIndexLocal));
    il.Append(il.Create(OpCodes.Ldelem_Ref));
    il.Append(il.Create(OpCodes.Stloc, skinnedLocal));
    il.Append(il.Create(OpCodes.Ldloc, skinnedLocal));
    il.Append(il.Create(OpCodes.Brfalse, rendererLoopContinue));
    il.Append(il.Create(OpCodes.Ldloc, skinnedLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Callvirt, module.ImportReference(setRendererEnabled)));
    il.Append(il.Create(OpCodes.Ldloc, skinnedLocal));
    il.Append(il.Create(OpCodes.Callvirt, module.ImportReference(getSharedMesh)));
    il.Append(il.Create(OpCodes.Stloc, meshLocal));
    il.Append(il.Create(OpCodes.Ldloc, meshLocal));
    il.Append(il.Create(OpCodes.Ldnull));
    il.Append(il.Create(OpCodes.Call, module.ImportReference(objectInequality)));
    il.Append(il.Create(OpCodes.Brfalse, rendererLoopContinue));
    il.Append(il.Create(OpCodes.Ldc_I4_0));
    il.Append(il.Create(OpCodes.Stloc, shapeIndexLocal));
    il.Append(il.Create(OpCodes.Br, shapeLoopCheck));

    il.Append(shapeLoopStart);
    il.Append(il.Create(OpCodes.Ldloc, meshLocal));
    il.Append(il.Create(OpCodes.Ldloc, shapeIndexLocal));
    il.Append(il.Create(OpCodes.Callvirt, module.ImportReference(getBlendShapeName)));
    il.Append(il.Create(OpCodes.Stloc, shapeNameLocal));
    il.Append(il.Create(OpCodes.Ldloc, shapeNameLocal));
    il.Append(il.Create(OpCodes.Call, isOpenMouthBlendshape));
    il.Append(il.Create(OpCodes.Brfalse, shapeLoopContinue));
    il.Append(il.Create(OpCodes.Ldloc, skinnedLocal));
    il.Append(il.Create(OpCodes.Ldloc, shapeIndexLocal));
    il.Append(il.Create(OpCodes.Ldloc, weightLocal));
    il.Append(il.Create(OpCodes.Callvirt, module.ImportReference(setBlendShapeWeight)));

    il.Append(shapeLoopContinue);
    il.Append(il.Create(OpCodes.Ldloc, shapeIndexLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Add));
    il.Append(il.Create(OpCodes.Stloc, shapeIndexLocal));
    il.Append(shapeLoopCheck);
    il.Append(il.Create(OpCodes.Ldloc, shapeIndexLocal));
    il.Append(il.Create(OpCodes.Ldloc, meshLocal));
    il.Append(il.Create(OpCodes.Callvirt, module.ImportReference(getBlendShapeCount)));
    il.Append(il.Create(OpCodes.Blt, shapeLoopStart));
    il.Append(skipRendererShapes);

    il.Append(rendererLoopContinue);
    il.Append(il.Create(OpCodes.Ldloc, skinnedIndexLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Add));
    il.Append(il.Create(OpCodes.Stloc, skinnedIndexLocal));
    il.Append(rendererLoopCheck);
    il.Append(il.Create(OpCodes.Ldloc, skinnedIndexLocal));
    il.Append(il.Create(OpCodes.Ldloc, skinnedArrayLocal));
    il.Append(il.Create(OpCodes.Ldlen));
    il.Append(il.Create(OpCodes.Conv_I4));
    il.Append(il.Create(OpCodes.Blt, rendererLoopStart));
    il.Append(il.Create(OpCodes.Ret));

    changed = true;
    return method;
}

static MethodDefinition EnsureRuntimeApplyForm(ModuleDefinition module, ref bool changed, out MethodDefinition applyFormFrame)
{
    var runtimeType = FindType(module, RuntimeTypeName)
        ?? throw new InvalidOperationException("Natsume runtime type not found.");
    var currentFormNameField = runtimeType.Fields.FirstOrDefault(field =>
        field.Name == CurrentFormNameFieldName
        && field.FieldType.FullName == module.TypeSystem.String.FullName
    );
    if (currentFormNameField == null)
    {
        currentFormNameField = new FieldDefinition(
            CurrentFormNameFieldName,
            FieldAttributes.Public | FieldAttributes.Static,
            module.TypeSystem.String
        );
        runtimeType.Fields.Add(currentFormNameField);
        changed = true;
    }
    else if ((currentFormNameField.Attributes & FieldAttributes.FieldAccessMask) != FieldAttributes.Public)
    {
        currentFormNameField.Attributes &= ~FieldAttributes.FieldAccessMask;
        currentFormNameField.Attributes |= FieldAttributes.Public | FieldAttributes.Static;
        changed = true;
    }

    var currentFormWeightField = runtimeType.Fields.FirstOrDefault(field =>
        field.Name == CurrentFormWeightFieldName
        && field.FieldType.FullName == module.TypeSystem.Single.FullName
    );
    if (currentFormWeightField == null)
    {
        currentFormWeightField = new FieldDefinition(
            CurrentFormWeightFieldName,
            FieldAttributes.Public | FieldAttributes.Static,
            module.TypeSystem.Single
        );
        runtimeType.Fields.Add(currentFormWeightField);
        changed = true;
    }
    else if ((currentFormWeightField.Attributes & FieldAttributes.FieldAccessMask) != FieldAttributes.Public)
    {
        currentFormWeightField.Attributes &= ~FieldAttributes.FieldAccessMask;
        currentFormWeightField.Attributes |= FieldAttributes.Public | FieldAttributes.Static;
        changed = true;
    }

    var clamp01 = FindMethodReference(module, method => method.FullName == "System.Single UnityEngine.Mathf::Clamp01(System.Single)")
        ?? throw new InvalidOperationException("UnityEngine.Mathf.Clamp01(float) not found.");

    var method = runtimeType.Methods.FirstOrDefault(candidate =>
        candidate.Name == ApplyFormMethodName
        && candidate.Parameters.Count == 2
        && candidate.Parameters[0].ParameterType.FullName == module.TypeSystem.String.FullName
        && candidate.Parameters[1].ParameterType.FullName == module.TypeSystem.Single.FullName
    );
    if (method == null)
    {
        method = new MethodDefinition(
            ApplyFormMethodName,
            MethodAttributes.Public | MethodAttributes.Static | MethodAttributes.HideBySig,
            module.TypeSystem.Void
        );
        method.Parameters.Add(new ParameterDefinition("blendshapeName", ParameterAttributes.None, module.TypeSystem.String));
        method.Parameters.Add(new ParameterDefinition("value", ParameterAttributes.None, module.TypeSystem.Single));
        runtimeType.Methods.Add(method);
    }
    else
    {
        method.Body = new MethodBody(method);
    }

    method.Body.InitLocals = true;
    var weightLocal = new VariableDefinition(module.TypeSystem.Single);
    method.Body.Variables.Add(weightLocal);
    var il = method.Body.GetILProcessor();
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Stsfld, currentFormNameField));
    il.Append(il.Create(OpCodes.Ldarg_1));
    il.Append(il.Create(OpCodes.Call, module.ImportReference(clamp01)));
    il.Append(il.Create(OpCodes.Stloc, weightLocal));
    il.Append(il.Create(OpCodes.Ldloc, weightLocal));
    il.Append(il.Create(OpCodes.Stsfld, currentFormWeightField));
    il.Append(il.Create(OpCodes.Ret));

    applyFormFrame = runtimeType.Methods.FirstOrDefault(candidate =>
        candidate.Name == ApplyFormFrameMethodName
        && candidate.Parameters.Count == 0
    ) ?? new MethodDefinition(
        ApplyFormFrameMethodName,
        MethodAttributes.Public | MethodAttributes.Static | MethodAttributes.HideBySig,
        module.TypeSystem.Void
    );
    if (!runtimeType.Methods.Contains(applyFormFrame))
    {
        runtimeType.Methods.Add(applyFormFrame);
    }
    else
    {
        applyFormFrame.Body = new MethodBody(applyFormFrame);
    }

    applyFormFrame.Body.InitLocals = false;
    var frameIl = applyFormFrame.Body.GetILProcessor();
    frameIl.Append(frameIl.Create(OpCodes.Ret));

    changed = true;
    return method;
}

static MethodDefinition EnsureRuntimeApplyFormInputs(ModuleDefinition module, ref bool changed)
{
    var runtimeType = FindType(module, RuntimeTypeName)
        ?? throw new InvalidOperationException("Natsume runtime type not found.");
    var universalBlendshapes = FindType(module, "UniversalBlendshapes")
        ?? throw new InvalidOperationException("UniversalBlendshapes type not found.");
    var currentFormNameField = runtimeType.Fields.First(field => field.Name == CurrentFormNameFieldName);
    var currentFormWeightField = runtimeType.Fields.First(field => field.Name == CurrentFormWeightFieldName);
    var stringType = module.TypeSystem.String.Resolve();
    var stringIsNullOrEmpty = stringType.Methods.First(method =>
        method.Name == "IsNullOrEmpty"
        && method.Parameters.Count == 1
        && method.Parameters[0].ParameterType.FullName == module.TypeSystem.String.FullName
    );
    var stringContains = stringType.Methods.First(method =>
        method.Name == "Contains"
        && method.Parameters.Count == 1
        && method.Parameters[0].ParameterType.FullName == module.TypeSystem.String.FullName
        && method.ReturnType.FullName == module.TypeSystem.Boolean.FullName
    );

    var controlledFields = new[]
    {
        "Joy",
        "Angry",
        "Sorrow",
        "Fun",
        "O",
        "LookUp",
        "LookDown",
        "LookLeft",
        "LookRight",
    }
        .Select(name => universalBlendshapes.Fields.First(field => field.Name == name))
        .ToArray();

    var method = runtimeType.Methods.FirstOrDefault(candidate =>
        candidate.Name == ApplyFormInputsMethodName
        && candidate.Parameters.Count == 1
    ) ?? new MethodDefinition(
        ApplyFormInputsMethodName,
        MethodAttributes.Public | MethodAttributes.Static | MethodAttributes.HideBySig,
        module.TypeSystem.Void
    );
    if (!runtimeType.Methods.Contains(method))
    {
        method.Parameters.Add(new ParameterDefinition("blendshapes", ParameterAttributes.None, universalBlendshapes));
        runtimeType.Methods.Add(method);
    }
    else
    {
        method.Parameters.Clear();
        method.Parameters.Add(new ParameterDefinition("blendshapes", ParameterAttributes.None, universalBlendshapes));
        method.Body = new MethodBody(method);
    }

    method.Body.InitLocals = true;
    var nameLocal = new VariableDefinition(module.TypeSystem.String);
    var weightLocal = new VariableDefinition(module.TypeSystem.Single);
    method.Body.Variables.Add(nameLocal);
    method.Body.Variables.Add(weightLocal);
    var il = method.Body.GetILProcessor();
    var continueAfterName = il.Create(OpCodes.Nop);
    var returnInstruction = il.Create(OpCodes.Ret);

    foreach (var field in controlledFields)
    {
        il.Append(il.Create(OpCodes.Ldarg_0));
        il.Append(il.Create(OpCodes.Ldc_R4, 0.0f));
        il.Append(il.Create(OpCodes.Stfld, field));
    }

    il.Append(il.Create(OpCodes.Ldsfld, currentFormNameField));
    il.Append(il.Create(OpCodes.Dup));
    il.Append(il.Create(OpCodes.Stloc, nameLocal));
    il.Append(il.Create(OpCodes.Call, module.ImportReference(stringIsNullOrEmpty)));
    il.Append(il.Create(OpCodes.Brfalse, continueAfterName));
    il.Append(il.Create(OpCodes.Ret));
    il.Append(continueAfterName);
    il.Append(il.Create(OpCodes.Ldsfld, currentFormWeightField));
    il.Append(il.Create(OpCodes.Stloc, weightLocal));

    AddFormInputRule("Angry", "Angry");
    AddFormInputRule("Sorrow", "Sorrow");
    AddFormInputRule("Cry", "Sorrow");
    AddFormInputRule("Dark", "Sorrow");
    AddFormInputRule("Fun", "Fun");
    AddFormInputRule("Star", "Fun");
    AddFormInputRule("Neko", "Fun");
    AddFormInputRule("Red", "Fun");
    AddFormInputRule("Joy", "Joy");
    AddFormInputRule("Heart", "Joy");
    AddFormInputRule("Smile", "Joy");
    AddFormInputRule(":O", "O");
    AddFormInputRule("LookUp", "LookUp");
    AddFormInputRule("LookDown", "LookDown");
    AddFormInputRule("LookLeft", "LookLeft");
    AddFormInputRule("LookRight", "LookRight");
    il.Append(returnInstruction);

    changed = true;
    return method;

    void AddFormInputRule(string token, string fieldName)
    {
        var next = il.Create(OpCodes.Nop);
        var targetField = universalBlendshapes.Fields.First(field => field.Name == fieldName);
        il.Append(il.Create(OpCodes.Ldloc, nameLocal));
        il.Append(il.Create(OpCodes.Ldstr, token));
        il.Append(il.Create(OpCodes.Callvirt, module.ImportReference(stringContains)));
        il.Append(il.Create(OpCodes.Brfalse, next));
        il.Append(il.Create(OpCodes.Ldarg_0));
        il.Append(il.Create(OpCodes.Ldloc, weightLocal));
        il.Append(il.Create(OpCodes.Stfld, targetField));
        il.Append(il.Create(OpCodes.Ret));
        il.Append(next);
    }
}

static MethodDefinition EnsureRuntimeSetBlendshapeWeightByName(ModuleDefinition module, TypeDefinition runtimeType, ref bool changed)
{
    const string methodName = "SetBlendshapeWeightByName";
    var skinnedFindGeneric = EnumerateMethodReferences(module)
        .OfType<GenericInstanceMethod>()
        .First(method =>
            method.ElementMethod.Name == "FindObjectsOfType"
            && method.ElementMethod.Parameters.Count == 1
            && method.GenericArguments.Count == 1
            && method.GenericArguments[0].FullName == "UnityEngine.SkinnedMeshRenderer"
        );
    var skinnedRendererType = skinnedFindGeneric.GenericArguments[0];
    var findSkinnedTemplate = skinnedFindGeneric.ElementMethod;
    var getSharedMesh = FindMethodReference(module, method => method.FullName == "UnityEngine.Mesh UnityEngine.SkinnedMeshRenderer::get_sharedMesh()")
        ?? throw new InvalidOperationException("SkinnedMeshRenderer.sharedMesh getter not found.");
    var setRendererEnabled = FindMethodReference(module, method => method.FullName == "System.Void UnityEngine.Renderer::set_enabled(System.Boolean)")
        ?? throw new InvalidOperationException("Renderer.enabled setter not found.");
    var getBlendShapeCount = FindMethodReference(module, method => method.FullName == "System.Int32 UnityEngine.Mesh::get_blendShapeCount()")
        ?? throw new InvalidOperationException("Mesh.blendShapeCount getter not found.");
    var getBlendShapeName = FindMethodReference(module, method => method.FullName == "System.String UnityEngine.Mesh::GetBlendShapeName(System.Int32)")
        ?? throw new InvalidOperationException("Mesh.GetBlendShapeName(int) not found.");
    var setBlendShapeWeight = FindMethodReference(module, method => method.FullName == "System.Void UnityEngine.SkinnedMeshRenderer::SetBlendShapeWeight(System.Int32,System.Single)")
        ?? throw new InvalidOperationException("SkinnedMeshRenderer.SetBlendShapeWeight(int,float) not found.");
    var objectInequality = FindMethodReference(module, method => method.FullName == "System.Boolean UnityEngine.Object::op_Inequality(UnityEngine.Object,UnityEngine.Object)")
        ?? throw new InvalidOperationException("UnityEngine.Object.op_Inequality not found.");
    var stringEquality = module.TypeSystem.String.Resolve().Methods.First(method =>
        method.Name == "op_Equality"
        && method.Parameters.Count == 2
        && method.Parameters[0].ParameterType.FullName == module.TypeSystem.String.FullName
        && method.Parameters[1].ParameterType.FullName == module.TypeSystem.String.FullName
    );
    var stringIsNullOrEmpty = module.TypeSystem.String.Resolve().Methods.First(method =>
        method.Name == "IsNullOrEmpty"
        && method.Parameters.Count == 1
        && method.Parameters[0].ParameterType.FullName == module.TypeSystem.String.FullName
    );

    var method = runtimeType.Methods.FirstOrDefault(candidate =>
        candidate.Name == methodName
        && candidate.Parameters.Count == 2
        && candidate.Parameters[0].ParameterType.FullName == module.TypeSystem.String.FullName
        && candidate.Parameters[1].ParameterType.FullName == module.TypeSystem.Single.FullName
    );
    if (method == null)
    {
        method = new MethodDefinition(
            methodName,
            MethodAttributes.Private | MethodAttributes.Static | MethodAttributes.HideBySig,
            module.TypeSystem.Void
        );
        method.Parameters.Add(new ParameterDefinition("blendshapeName", ParameterAttributes.None, module.TypeSystem.String));
        method.Parameters.Add(new ParameterDefinition("weight", ParameterAttributes.None, module.TypeSystem.Single));
        runtimeType.Methods.Add(method);
    }
    else
    {
        method.Body = new MethodBody(method);
    }

    method.Body.InitLocals = true;
    var skinnedArrayLocal = new VariableDefinition(new ArrayType(skinnedRendererType));
    var skinnedIndexLocal = new VariableDefinition(module.TypeSystem.Int32);
    var skinnedLocal = new VariableDefinition(skinnedRendererType);
    var meshLocal = new VariableDefinition(getSharedMesh.ReturnType);
    var shapeIndexLocal = new VariableDefinition(module.TypeSystem.Int32);
    var shapeNameLocal = new VariableDefinition(module.TypeSystem.String);
    method.Body.Variables.Add(skinnedArrayLocal);
    method.Body.Variables.Add(skinnedIndexLocal);
    method.Body.Variables.Add(skinnedLocal);
    method.Body.Variables.Add(meshLocal);
    method.Body.Variables.Add(shapeIndexLocal);
    method.Body.Variables.Add(shapeNameLocal);

    var findSkinned = new GenericInstanceMethod(module.ImportReference(findSkinnedTemplate));
    findSkinned.GenericArguments.Add(skinnedRendererType);
    var il = method.Body.GetILProcessor();
    var rendererLoopStart = il.Create(OpCodes.Nop);
    var rendererLoopContinue = il.Create(OpCodes.Nop);
    var rendererLoopCheck = il.Create(OpCodes.Nop);
    var shapeLoopStart = il.Create(OpCodes.Nop);
    var shapeLoopContinue = il.Create(OpCodes.Nop);
    var shapeLoopCheck = il.Create(OpCodes.Nop);
    var continueAfterName = il.Create(OpCodes.Nop);

    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, module.ImportReference(stringIsNullOrEmpty)));
    il.Append(il.Create(OpCodes.Brfalse, continueAfterName));
    il.Append(il.Create(OpCodes.Ret));
    il.Append(continueAfterName);
    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Call, findSkinned));
    il.Append(il.Create(OpCodes.Stloc, skinnedArrayLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_0));
    il.Append(il.Create(OpCodes.Stloc, skinnedIndexLocal));
    il.Append(il.Create(OpCodes.Br, rendererLoopCheck));

    il.Append(rendererLoopStart);
    il.Append(il.Create(OpCodes.Ldloc, skinnedArrayLocal));
    il.Append(il.Create(OpCodes.Ldloc, skinnedIndexLocal));
    il.Append(il.Create(OpCodes.Ldelem_Ref));
    il.Append(il.Create(OpCodes.Stloc, skinnedLocal));
    il.Append(il.Create(OpCodes.Ldloc, skinnedLocal));
    il.Append(il.Create(OpCodes.Brfalse, rendererLoopContinue));
    il.Append(il.Create(OpCodes.Ldloc, skinnedLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Callvirt, module.ImportReference(setRendererEnabled)));
    il.Append(il.Create(OpCodes.Ldloc, skinnedLocal));
    il.Append(il.Create(OpCodes.Callvirt, module.ImportReference(getSharedMesh)));
    il.Append(il.Create(OpCodes.Stloc, meshLocal));
    il.Append(il.Create(OpCodes.Ldloc, meshLocal));
    il.Append(il.Create(OpCodes.Ldnull));
    il.Append(il.Create(OpCodes.Call, module.ImportReference(objectInequality)));
    il.Append(il.Create(OpCodes.Brfalse, rendererLoopContinue));
    il.Append(il.Create(OpCodes.Ldc_I4_0));
    il.Append(il.Create(OpCodes.Stloc, shapeIndexLocal));
    il.Append(il.Create(OpCodes.Br, shapeLoopCheck));

    il.Append(shapeLoopStart);
    il.Append(il.Create(OpCodes.Ldloc, meshLocal));
    il.Append(il.Create(OpCodes.Ldloc, shapeIndexLocal));
    il.Append(il.Create(OpCodes.Callvirt, module.ImportReference(getBlendShapeName)));
    il.Append(il.Create(OpCodes.Stloc, shapeNameLocal));
    il.Append(il.Create(OpCodes.Ldloc, shapeNameLocal));
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, module.ImportReference(stringEquality)));
    il.Append(il.Create(OpCodes.Brfalse, shapeLoopContinue));
    il.Append(il.Create(OpCodes.Ldloc, skinnedLocal));
    il.Append(il.Create(OpCodes.Ldloc, shapeIndexLocal));
    il.Append(il.Create(OpCodes.Ldarg_1));
    il.Append(il.Create(OpCodes.Callvirt, module.ImportReference(setBlendShapeWeight)));

    il.Append(shapeLoopContinue);
    il.Append(il.Create(OpCodes.Ldloc, shapeIndexLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Add));
    il.Append(il.Create(OpCodes.Stloc, shapeIndexLocal));
    il.Append(shapeLoopCheck);
    il.Append(il.Create(OpCodes.Ldloc, shapeIndexLocal));
    il.Append(il.Create(OpCodes.Ldloc, meshLocal));
    il.Append(il.Create(OpCodes.Callvirt, module.ImportReference(getBlendShapeCount)));
    il.Append(il.Create(OpCodes.Blt, shapeLoopStart));

    il.Append(rendererLoopContinue);
    il.Append(il.Create(OpCodes.Ldloc, skinnedIndexLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Add));
    il.Append(il.Create(OpCodes.Stloc, skinnedIndexLocal));
    il.Append(rendererLoopCheck);
    il.Append(il.Create(OpCodes.Ldloc, skinnedIndexLocal));
    il.Append(il.Create(OpCodes.Ldloc, skinnedArrayLocal));
    il.Append(il.Create(OpCodes.Ldlen));
    il.Append(il.Create(OpCodes.Conv_I4));
    il.Append(il.Create(OpCodes.Blt, rendererLoopStart));
    il.Append(il.Create(OpCodes.Ret));

    changed = true;
    return method;
}

static MethodDefinition EnsureRuntimeApplyFreeze(ModuleDefinition module, ref bool changed)
{
    var runtimeType = FindType(module, RuntimeTypeName)
        ?? throw new InvalidOperationException("Natsume runtime type not found.");
    var freezeField = runtimeType.Fields.FirstOrDefault(field =>
        field.Name == FreezeMotionFieldName
        && field.FieldType.FullName == module.TypeSystem.Boolean.FullName
    );
    if (freezeField == null)
    {
        freezeField = new FieldDefinition(
            FreezeMotionFieldName,
            FieldAttributes.Public | FieldAttributes.Static,
            module.TypeSystem.Boolean
        );
        runtimeType.Fields.Add(freezeField);
        changed = true;
    }
    else if ((freezeField.Attributes & FieldAttributes.FieldAccessMask) != FieldAttributes.Public)
    {
        freezeField.Attributes &= ~FieldAttributes.FieldAccessMask;
        freezeField.Attributes |= FieldAttributes.Public | FieldAttributes.Static;
        changed = true;
    }

    var method = runtimeType.Methods.FirstOrDefault(candidate =>
        candidate.Name == ApplyFreezeMethodName
        && candidate.Parameters.Count == 1
        && candidate.Parameters[0].ParameterType.FullName == module.TypeSystem.Single.FullName
    );
    if (method == null)
    {
        method = new MethodDefinition(
            ApplyFreezeMethodName,
            MethodAttributes.Public | MethodAttributes.Static | MethodAttributes.HideBySig,
            module.TypeSystem.Void
        );
        method.Parameters.Add(new ParameterDefinition("value", ParameterAttributes.None, module.TypeSystem.Single));
        runtimeType.Methods.Add(method);
    }
    else
    {
        method.Body = new MethodBody(method);
    }

    method.Body.InitLocals = false;
    var il = method.Body.GetILProcessor();
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Ldc_R4, 0.49f));
    il.Append(il.Create(OpCodes.Cgt));
    il.Append(il.Create(OpCodes.Stsfld, freezeField));
    il.Append(il.Create(OpCodes.Ret));

    changed = true;
    return method;
}

static MethodDefinition EnsureRuntimeApplyFreezeFrame(ModuleDefinition module, ref bool changed)
{
    var runtimeType = FindType(module, RuntimeTypeName)
        ?? throw new InvalidOperationException("Natsume runtime type not found.");
    var freezeField = runtimeType.Fields.FirstOrDefault(field =>
        field.Name == FreezeMotionFieldName
        && field.FieldType.FullName == module.TypeSystem.Boolean.FullName
    ) ?? throw new InvalidOperationException("Natsume freeze motion field not found.");
    var setAnimatorSpeed = FindMethodReference(module, method => method.FullName == "System.Void UnityEngine.Animator::set_speed(System.Single)")
        ?? throw new InvalidOperationException("UnityEngine.Animator.speed setter not found.");
    var animatorType = setAnimatorSpeed.DeclaringType;
    var findAnimatorTemplate = EnumerateMethodReferences(module)
        .OfType<GenericInstanceMethod>()
        .First(method =>
            method.ElementMethod.Name == "FindObjectsOfType"
            && method.ElementMethod.Parameters.Count == 1
            && method.GenericArguments.Count == 1
        )
        .ElementMethod;

    var method = runtimeType.Methods.FirstOrDefault(candidate =>
        candidate.Name == ApplyFreezeFrameMethodName
        && candidate.Parameters.Count == 0
    );
    if (method == null)
    {
        method = new MethodDefinition(
            ApplyFreezeFrameMethodName,
            MethodAttributes.Public | MethodAttributes.Static | MethodAttributes.HideBySig,
            module.TypeSystem.Void
        );
        runtimeType.Methods.Add(method);
    }
    else
    {
        method.Body = new MethodBody(method);
    }

    method.Body.InitLocals = true;
    var animatorArrayLocal = new VariableDefinition(new ArrayType(animatorType));
    var animatorIndexLocal = new VariableDefinition(module.TypeSystem.Int32);
    var animatorLocal = new VariableDefinition(animatorType);
    method.Body.Variables.Add(animatorArrayLocal);
    method.Body.Variables.Add(animatorIndexLocal);
    method.Body.Variables.Add(animatorLocal);

    var findAnimators = new GenericInstanceMethod(module.ImportReference(findAnimatorTemplate));
    findAnimators.GenericArguments.Add(animatorType);
    var il = method.Body.GetILProcessor();
    var loopStart = il.Create(OpCodes.Nop);
    var loopContinue = il.Create(OpCodes.Nop);
    var loopCheck = il.Create(OpCodes.Nop);
    var setNormalSpeed = il.Create(OpCodes.Nop);
    var setSpeed = il.Create(OpCodes.Nop);

    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Call, findAnimators));
    il.Append(il.Create(OpCodes.Stloc, animatorArrayLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_0));
    il.Append(il.Create(OpCodes.Stloc, animatorIndexLocal));
    il.Append(il.Create(OpCodes.Br, loopCheck));

    il.Append(loopStart);
    il.Append(il.Create(OpCodes.Ldloc, animatorArrayLocal));
    il.Append(il.Create(OpCodes.Ldloc, animatorIndexLocal));
    il.Append(il.Create(OpCodes.Ldelem_Ref));
    il.Append(il.Create(OpCodes.Stloc, animatorLocal));
    il.Append(il.Create(OpCodes.Ldloc, animatorLocal));
    il.Append(il.Create(OpCodes.Brfalse, loopContinue));
    il.Append(il.Create(OpCodes.Ldloc, animatorLocal));
    il.Append(il.Create(OpCodes.Ldsfld, freezeField));
    il.Append(il.Create(OpCodes.Brfalse, setNormalSpeed));
    il.Append(il.Create(OpCodes.Ldc_R4, 0.0f));
    il.Append(il.Create(OpCodes.Br, setSpeed));
    il.Append(setNormalSpeed);
    il.Append(il.Create(OpCodes.Ldc_R4, 1.0f));
    il.Append(setSpeed);
    il.Append(il.Create(OpCodes.Callvirt, module.ImportReference(setAnimatorSpeed)));

    il.Append(loopContinue);
    il.Append(il.Create(OpCodes.Ldloc, animatorIndexLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Add));
    il.Append(il.Create(OpCodes.Stloc, animatorIndexLocal));
    il.Append(loopCheck);
    il.Append(il.Create(OpCodes.Ldloc, animatorIndexLocal));
    il.Append(il.Create(OpCodes.Ldloc, animatorArrayLocal));
    il.Append(il.Create(OpCodes.Ldlen));
    il.Append(il.Create(OpCodes.Conv_I4));
    il.Append(il.Create(OpCodes.Blt, loopStart));
    il.Append(il.Create(OpCodes.Ret));

    changed = true;
    return method;
}

static bool IsNativeActionControlEnabled()
{
    return false;
}

static MethodDefinition EnsureRuntimeApplyAction(ModuleDefinition module, ref bool changed)
{
    var runtimeType = FindType(module, RuntimeTypeName);
    if (runtimeType == null)
    {
        runtimeType = new TypeDefinition(
            "",
            RuntimeTypeName,
            TypeAttributes.NotPublic
            | TypeAttributes.Abstract
            | TypeAttributes.Sealed
            | TypeAttributes.BeforeFieldInit,
            module.TypeSystem.Object
        );
        module.Types.Add(runtimeType);
        changed = true;
    }

    if (!IsNativeActionControlEnabled())
    {
        var disabledMethod = runtimeType.Methods.FirstOrDefault(candidate =>
            candidate.Name == ApplyActionMethodName
            && candidate.Parameters.Count == 2
            && candidate.Parameters[0].ParameterType.FullName == module.TypeSystem.String.FullName
            && candidate.Parameters[1].ParameterType.FullName == module.TypeSystem.Single.FullName
        );
        if (disabledMethod == null)
        {
            disabledMethod = new MethodDefinition(
                ApplyActionMethodName,
                MethodAttributes.Public | MethodAttributes.Static | MethodAttributes.HideBySig,
                module.TypeSystem.Void
            );
            disabledMethod.Parameters.Add(new ParameterDefinition("action", ParameterAttributes.None, module.TypeSystem.String));
            disabledMethod.Parameters.Add(new ParameterDefinition("value", ParameterAttributes.None, module.TypeSystem.Single));
            runtimeType.Methods.Add(disabledMethod);
        }
        else
        {
            disabledMethod.Body = new MethodBody(disabledMethod);
        }

        var disabledIl = disabledMethod.Body.GetILProcessor();
        disabledIl.Append(disabledIl.Create(OpCodes.Ret));
        changed = true;
        return disabledMethod;
    }

    var avatarAnimatorController = FindType(module, "AvatarAnimatorController")
        ?? throw new InvalidOperationException("Type AvatarAnimatorController not found.");
    var avatarFoodController = FindType(module, "AvatarFoodController")
        ?? throw new InvalidOperationException("Type AvatarFoodController not found.");
    var avatarDanceHandler = FindType(module, "CustomDancePlayer.AvatarDanceHandler")
        ?? FindType(module, "AvatarDanceHandler")
        ?? throw new InvalidOperationException("Type CustomDancePlayer.AvatarDanceHandler not found.");
    var avatarSleepController = FindType(module, "AvatarSleepController")
        ?? throw new InvalidOperationException("Type AvatarSleepController not found.");
    var avatarLocomotionController = FindType(module, "AvatarLocomotionController")
        ?? throw new InvalidOperationException("Type AvatarLocomotionController not found.");
    var findObjectsTemplate = FindGenericMethodTemplate(module, "FindObjectsOfTypeAll", parameterCount: 0)
        ?? throw new InvalidOperationException("UnityEngine.Resources.FindObjectsOfTypeAll<T>() not found.");
    var setDancing = avatarAnimatorController.Methods.First(method =>
        method.Name == "SetDancing"
        && method.Parameters.Count == 1
        && method.Parameters[0].ParameterType.FullName == module.TypeSystem.Boolean.FullName
    );
    var startDancing = avatarAnimatorController.Methods.First(method =>
        method.Name == "StartDancing"
        && method.Parameters.Count == 0
    );
    var setDragging = avatarAnimatorController.Methods.First(method =>
        method.Name == "SetDragging"
        && method.Parameters.Count == 1
        && method.Parameters[0].ParameterType.FullName == module.TypeSystem.Boolean.FullName
    );
    var playInteract = avatarFoodController.Methods.First(method =>
        method.Name == "PlayInteract"
        && method.Parameters.Count == 0
    );
    var playDanceIndex = avatarDanceHandler.Methods.First(method =>
        method.Name == "PlayIndex"
        && method.Parameters.Count == 1
        && method.Parameters[0].ParameterType.FullName == module.TypeSystem.Int32.FullName
    );
    var stopDancePlay = avatarDanceHandler.Methods.First(method =>
        method.Name == "StopPlay"
        && method.Parameters.Count == 0
    );
    var setSleeping = avatarSleepController.Methods.First(method =>
        method.Name == "SetSleeping"
        && method.Parameters.Count == 1
        && method.Parameters[0].ParameterType.FullName == module.TypeSystem.Boolean.FullName
    );
    var forceStartWalk = avatarLocomotionController.Methods.First(method =>
        method.Name == "ForceStartWalk"
        && method.Parameters.Count == 0
    );
    var startWalk = avatarLocomotionController.Methods.First(method =>
        method.Name == "StartWalk"
        && method.Parameters.Count == 1
        && method.Parameters[0].ParameterType.FullName == module.TypeSystem.Boolean.FullName
    );
    var animatorField = avatarAnimatorController.Fields.First(field => field.Name == "animator");
    var danceStateField = avatarAnimatorController.Fields.First(field => field.Name == "danceState");
    var danceIndexParamField = avatarAnimatorController.Fields.First(field => field.Name == "danceIndexParam");
    var animatorSetFloat = avatarAnimatorController.Methods
        .Where(method => method.HasBody)
        .SelectMany(method => method.Body.Instructions)
        .Select(instruction => instruction.Operand)
        .OfType<MethodReference>()
        .First(method =>
        method.Name == "SetFloat"
        && method.Parameters.Count == 2
        && method.Parameters[0].ParameterType.FullName == module.TypeSystem.Int32.FullName
        && method.Parameters[1].ParameterType.FullName == module.TypeSystem.Single.FullName
    );

    var indexedDanceActions = new List<(int Index, MethodDefinition Match)>();
    for (var danceIndex = 0; danceIndex < 10; danceIndex++)
    {
        var match = EnsureStringMatchMethod(
            module,
            runtimeType,
            $"IsNatsumeDanceIndex{danceIndex}Action",
            new[]
            {
                $"dance{danceIndex}",
                $"dance_{danceIndex}",
                $"dance-index-{danceIndex}",
                $"pose{danceIndex}",
                $"pose_{danceIndex}",
                $"native_dance_{danceIndex}",
            },
            ref changed
        );
        indexedDanceActions.Add((danceIndex, match));
    }

    var customDanceIndexActions = new List<(int Index, MethodDefinition Match)>();
    for (var danceIndex = 0; danceIndex < 10; danceIndex++)
    {
        var match = EnsureStringMatchMethod(
            module,
            runtimeType,
            $"IsNatsumeCustomDanceIndex{danceIndex}Action",
            new[]
            {
                $"mmd{danceIndex}",
                $"mmd_{danceIndex}",
                $"custom_dance_{danceIndex}",
                $"showdance{danceIndex}",
                $"show_dance_{danceIndex}",
                $"perform{danceIndex}",
                $"perform_{danceIndex}",
            },
            ref changed
        );
        customDanceIndexActions.Add((danceIndex, match));
    }

    var isCustomDanceAction = EnsureStringMatchMethod(
        module,
        runtimeType,
        "IsNatsumeCustomDanceAction",
        new[]
        {
            "dance",
            "dancing",
            "start_dance",
            "mmd",
            "custom_dance",
            "show_dance",
            "perform",
            "stage",
        },
        ref changed
    );
    var isSleepAction = EnsureStringMatchMethod(
        module,
        runtimeType,
        "IsNatsumeSleepAction",
        new[]
        {
            "sleep",
            "nap",
            "rest",
            "sleepy",
            "tired",
        },
        ref changed
    );
    var isWakeAction = EnsureStringMatchMethod(
        module,
        runtimeType,
        "IsNatsumeWakeAction",
        new[]
        {
            "wake",
            "wake_up",
            "awake",
            "stand",
            "stand_up",
        },
        ref changed
    );
    var isWalkAction = EnsureStringMatchMethod(
        module,
        runtimeType,
        "IsNatsumeWalkAction",
        new[]
        {
            "walk",
            "move",
            "wander",
            "approach",
            "come_here",
        },
        ref changed
    );
    var isWalkLeftAction = EnsureStringMatchMethod(
        module,
        runtimeType,
        "IsNatsumeWalkLeftAction",
        new[]
        {
            "walk_left",
            "move_left",
            "left",
        },
        ref changed
    );
    var isWalkRightAction = EnsureStringMatchMethod(
        module,
        runtimeType,
        "IsNatsumeWalkRightAction",
        new[]
        {
            "walk_right",
            "move_right",
            "right",
        },
        ref changed
    );

    var isNodAction = EnsureStringMatchMethod(
        module,
        runtimeType,
        "IsNatsumeNodAction",
        new[]
        {
            "nod",
            "yes",
            "agree",
            "ack",
            "bow",
        },
        ref changed
    );
    var isWaveAction = EnsureStringMatchMethod(
        module,
        runtimeType,
        "IsNatsumeWaveAction",
        new[]
        {
            "wave",
            "hello",
            "hi",
            "greet",
            "bye",
        },
        ref changed
    );
    var isShakeAction = EnsureStringMatchMethod(
        module,
        runtimeType,
        "IsNatsumeShakeAction",
        new[]
        {
            "shake",
            "no",
            "deny",
            "refuse",
        },
        ref changed
    );
    var isBounceAction = EnsureStringMatchMethod(
        module,
        runtimeType,
        "IsNatsumeBounceAction",
        new[]
        {
            "bounce",
            "jump",
            "excited",
            "celebrate",
        },
        ref changed
    );
    var isSurprisedAction = EnsureStringMatchMethod(
        module,
        runtimeType,
        "IsNatsumeSurprisedAction",
        new[]
        {
            "surprised",
            "surprise",
            "startle",
            "shock",
        },
        ref changed
    );
    var isDanceAction = EnsureStringMatchMethod(
        module,
        runtimeType,
        "IsNatsumeDanceAction",
        new[]
        {
            "dance",
            "dancing",
            "start_dance",
            "bounce",
            "jump",
            "nod",
            "shake",
            "wave",
            "surprised",
            "surprise",
            "happy",
            "joy",
            "excited",
            "celebrate",
        },
        ref changed
    );
    var isStopAction = EnsureStringMatchMethod(
        module,
        runtimeType,
        "IsNatsumeStopAction",
        new[]
        {
            "stop",
            "idle",
            "home",
            "reset",
            "neutral",
            "stop_dance",
            "release",
            "undrag",
        },
        ref changed
    );
    var isDragAction = EnsureStringMatchMethod(
        module,
        runtimeType,
        "IsNatsumeDragAction",
        new[]
        {
            "drag",
            "dragging",
            "lean",
            "peek",
            "listen",
            "listening",
            "thinking",
            "focus",
        },
        ref changed
    );
    var isInteractAction = EnsureStringMatchMethod(
        module,
        runtimeType,
        "IsNatsumeInteractAction",
        new[]
        {
            "interact",
            "touch",
            "pat",
            "poke",
            "tap",
            "food",
            "feed",
        },
        ref changed
    );

    var method = runtimeType.Methods.FirstOrDefault(candidate =>
        candidate.Name == ApplyActionMethodName
        && candidate.Parameters.Count == 2
        && candidate.Parameters[0].ParameterType.FullName == module.TypeSystem.String.FullName
        && candidate.Parameters[1].ParameterType.FullName == module.TypeSystem.Single.FullName
    );
    if (method == null)
    {
        method = new MethodDefinition(
            ApplyActionMethodName,
            MethodAttributes.Public | MethodAttributes.Static | MethodAttributes.HideBySig,
            module.TypeSystem.Void
        );
        method.Parameters.Add(new ParameterDefinition("action", ParameterAttributes.None, module.TypeSystem.String));
        method.Parameters.Add(new ParameterDefinition("value", ParameterAttributes.None, module.TypeSystem.Single));
        runtimeType.Methods.Add(method);
    }
    else
    {
        method.Body = new MethodBody(method);
    }

    method.Body.InitLocals = true;
    var controllerArrayLocal = new VariableDefinition(new ArrayType(avatarAnimatorController));
    var controllerIndexLocal = new VariableDefinition(module.TypeSystem.Int32);
    var controllerLocal = new VariableDefinition(avatarAnimatorController);
    var foodArrayLocal = new VariableDefinition(new ArrayType(avatarFoodController));
    var foodIndexLocal = new VariableDefinition(module.TypeSystem.Int32);
    var foodLocal = new VariableDefinition(avatarFoodController);
    var danceHandlerArrayLocal = new VariableDefinition(new ArrayType(avatarDanceHandler));
    var danceHandlerIndexLocal = new VariableDefinition(module.TypeSystem.Int32);
    var danceHandlerLocal = new VariableDefinition(avatarDanceHandler);
    var sleepArrayLocal = new VariableDefinition(new ArrayType(avatarSleepController));
    var sleepIndexLocal = new VariableDefinition(module.TypeSystem.Int32);
    var sleepLocal = new VariableDefinition(avatarSleepController);
    var locomotionArrayLocal = new VariableDefinition(new ArrayType(avatarLocomotionController));
    var locomotionIndexLocal = new VariableDefinition(module.TypeSystem.Int32);
    var locomotionLocal = new VariableDefinition(avatarLocomotionController);
    method.Body.Variables.Add(controllerArrayLocal);
    method.Body.Variables.Add(controllerIndexLocal);
    method.Body.Variables.Add(controllerLocal);
    method.Body.Variables.Add(foodArrayLocal);
    method.Body.Variables.Add(foodIndexLocal);
    method.Body.Variables.Add(foodLocal);
    method.Body.Variables.Add(danceHandlerArrayLocal);
    method.Body.Variables.Add(danceHandlerIndexLocal);
    method.Body.Variables.Add(danceHandlerLocal);
    method.Body.Variables.Add(sleepArrayLocal);
    method.Body.Variables.Add(sleepIndexLocal);
    method.Body.Variables.Add(sleepLocal);
    method.Body.Variables.Add(locomotionArrayLocal);
    method.Body.Variables.Add(locomotionIndexLocal);
    method.Body.Variables.Add(locomotionLocal);

    var findControllers = new GenericInstanceMethod(module.ImportReference(findObjectsTemplate));
    findControllers.GenericArguments.Add(avatarAnimatorController);
    var findFoods = new GenericInstanceMethod(module.ImportReference(findObjectsTemplate));
    findFoods.GenericArguments.Add(avatarFoodController);
    var findDanceHandlers = new GenericInstanceMethod(module.ImportReference(findObjectsTemplate));
    findDanceHandlers.GenericArguments.Add(avatarDanceHandler);
    var findSleepControllers = new GenericInstanceMethod(module.ImportReference(findObjectsTemplate));
    findSleepControllers.GenericArguments.Add(avatarSleepController);
    var findLocomotionControllers = new GenericInstanceMethod(module.ImportReference(findObjectsTemplate));
    findLocomotionControllers.GenericArguments.Add(avatarLocomotionController);

    var il = method.Body.GetILProcessor();
    var notCustomDance = il.Create(OpCodes.Nop);
    var notSleep = il.Create(OpCodes.Nop);
    var notWake = il.Create(OpCodes.Nop);
    var notWalkLeft = il.Create(OpCodes.Nop);
    var notWalkRight = il.Create(OpCodes.Nop);
    var notWalk = il.Create(OpCodes.Nop);
    var notNod = il.Create(OpCodes.Nop);
    var notWave = il.Create(OpCodes.Nop);
    var notShake = il.Create(OpCodes.Nop);
    var notBounce = il.Create(OpCodes.Nop);
    var notSurprised = il.Create(OpCodes.Nop);
    var notDance = il.Create(OpCodes.Nop);
    var notStop = il.Create(OpCodes.Nop);
    var notDrag = il.Create(OpCodes.Nop);
    var notInteract = il.Create(OpCodes.Nop);
    var ret = il.Create(OpCodes.Ret);

    foreach (var customDanceIndexAction in customDanceIndexActions)
    {
        var notCustomDanceIndex = il.Create(OpCodes.Nop);
        il.Append(il.Create(OpCodes.Ldarg_0));
        il.Append(il.Create(OpCodes.Call, customDanceIndexAction.Match));
        il.Append(il.Create(OpCodes.Brfalse, notCustomDanceIndex));
        EmitDanceHandlerPlayLoop(
            il,
            findDanceHandlers,
            danceHandlerArrayLocal,
            danceHandlerIndexLocal,
            danceHandlerLocal,
            playDanceIndex,
            customDanceIndexAction.Index
        );
        il.Append(il.Create(OpCodes.Br, ret));
        il.Append(notCustomDanceIndex);
    }

    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, isCustomDanceAction));
    il.Append(il.Create(OpCodes.Brfalse, notCustomDance));
    EmitDanceHandlerPlayLoop(
        il,
        findDanceHandlers,
        danceHandlerArrayLocal,
        danceHandlerIndexLocal,
        danceHandlerLocal,
        playDanceIndex,
        danceIndex: 0
    );
    il.Append(il.Create(OpCodes.Br, ret));

    il.Append(notCustomDance);
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, isSleepAction));
    il.Append(il.Create(OpCodes.Brfalse, notSleep));
    EmitSleepControllerLoop(
        il,
        findSleepControllers,
        sleepArrayLocal,
        sleepIndexLocal,
        sleepLocal,
        setSleeping,
        sleeping: true
    );
    il.Append(il.Create(OpCodes.Br, ret));

    il.Append(notSleep);
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, isWakeAction));
    il.Append(il.Create(OpCodes.Brfalse, notWake));
    EmitSleepControllerLoop(
        il,
        findSleepControllers,
        sleepArrayLocal,
        sleepIndexLocal,
        sleepLocal,
        setSleeping,
        sleeping: false
    );
    il.Append(il.Create(OpCodes.Br, ret));

    il.Append(notWake);
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, isWalkLeftAction));
    il.Append(il.Create(OpCodes.Brfalse, notWalkLeft));
    EmitLocomotionWalkLoop(
        il,
        findLocomotionControllers,
        locomotionArrayLocal,
        locomotionIndexLocal,
        locomotionLocal,
        startWalk,
        direction: false
    );
    il.Append(il.Create(OpCodes.Br, ret));

    il.Append(notWalkLeft);
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, isWalkRightAction));
    il.Append(il.Create(OpCodes.Brfalse, notWalkRight));
    EmitLocomotionWalkLoop(
        il,
        findLocomotionControllers,
        locomotionArrayLocal,
        locomotionIndexLocal,
        locomotionLocal,
        startWalk,
        direction: true
    );
    il.Append(il.Create(OpCodes.Br, ret));

    il.Append(notWalkRight);
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, isWalkAction));
    il.Append(il.Create(OpCodes.Brfalse, notWalk));
    EmitLocomotionWalkLoop(
        il,
        findLocomotionControllers,
        locomotionArrayLocal,
        locomotionIndexLocal,
        locomotionLocal,
        forceStartWalk,
        direction: null
    );
    il.Append(il.Create(OpCodes.Br, ret));

    il.Append(notWalk);
    foreach (var indexedDanceAction in indexedDanceActions)
    {
        var notIndexedDance = il.Create(OpCodes.Nop);
        il.Append(il.Create(OpCodes.Ldarg_0));
        il.Append(il.Create(OpCodes.Call, indexedDanceAction.Match));
        il.Append(il.Create(OpCodes.Brfalse, notIndexedDance));
        EmitControllerIndexedDanceLoop(
            il,
            findControllers,
            controllerArrayLocal,
            controllerIndexLocal,
            controllerLocal,
            setDragging,
            setDancing,
            danceStateField,
            animatorField,
            danceIndexParamField,
            module.ImportReference(animatorSetFloat),
            danceIndex: indexedDanceAction.Index
        );
        il.Append(il.Create(OpCodes.Br, ret));
        il.Append(notIndexedDance);
    }

    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, isNodAction));
    il.Append(il.Create(OpCodes.Brfalse, notNod));
    EmitControllerIndexedDanceLoop(
        il,
        findControllers,
        controllerArrayLocal,
        controllerIndexLocal,
        controllerLocal,
        setDragging,
        setDancing,
        danceStateField,
        animatorField,
        danceIndexParamField,
        module.ImportReference(animatorSetFloat),
        danceIndex: 1
    );
    il.Append(il.Create(OpCodes.Br, ret));

    il.Append(notNod);
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, isWaveAction));
    il.Append(il.Create(OpCodes.Brfalse, notWave));
    EmitControllerIndexedDanceLoop(
        il,
        findControllers,
        controllerArrayLocal,
        controllerIndexLocal,
        controllerLocal,
        setDragging,
        setDancing,
        danceStateField,
        animatorField,
        danceIndexParamField,
        module.ImportReference(animatorSetFloat),
        danceIndex: 2
    );
    il.Append(il.Create(OpCodes.Br, ret));

    il.Append(notWave);
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, isShakeAction));
    il.Append(il.Create(OpCodes.Brfalse, notShake));
    EmitControllerIndexedDanceLoop(
        il,
        findControllers,
        controllerArrayLocal,
        controllerIndexLocal,
        controllerLocal,
        setDragging,
        setDancing,
        danceStateField,
        animatorField,
        danceIndexParamField,
        module.ImportReference(animatorSetFloat),
        danceIndex: 3
    );
    il.Append(il.Create(OpCodes.Br, ret));

    il.Append(notShake);
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, isBounceAction));
    il.Append(il.Create(OpCodes.Brfalse, notBounce));
    EmitControllerIndexedDanceLoop(
        il,
        findControllers,
        controllerArrayLocal,
        controllerIndexLocal,
        controllerLocal,
        setDragging,
        setDancing,
        danceStateField,
        animatorField,
        danceIndexParamField,
        module.ImportReference(animatorSetFloat),
        danceIndex: 4
    );
    il.Append(il.Create(OpCodes.Br, ret));

    il.Append(notBounce);
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, isSurprisedAction));
    il.Append(il.Create(OpCodes.Brfalse, notSurprised));
    EmitControllerIndexedDanceLoop(
        il,
        findControllers,
        controllerArrayLocal,
        controllerIndexLocal,
        controllerLocal,
        setDragging,
        setDancing,
        danceStateField,
        animatorField,
        danceIndexParamField,
        module.ImportReference(animatorSetFloat),
        danceIndex: 5
    );
    il.Append(il.Create(OpCodes.Br, ret));

    il.Append(notSurprised);
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, isDanceAction));
    il.Append(il.Create(OpCodes.Brfalse, notDance));
    EmitControllerIndexedDanceLoop(
        il,
        findControllers,
        controllerArrayLocal,
        controllerIndexLocal,
        controllerLocal,
        setDragging,
        setDancing,
        danceStateField,
        animatorField,
        danceIndexParamField,
        module.ImportReference(animatorSetFloat),
        danceIndex: 0
    );
    il.Append(il.Create(OpCodes.Br, ret));

    il.Append(notDance);
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, isStopAction));
    il.Append(il.Create(OpCodes.Brfalse, notStop));
    EmitDanceHandlerStopLoop(
        il,
        findDanceHandlers,
        danceHandlerArrayLocal,
        danceHandlerIndexLocal,
        danceHandlerLocal,
        stopDancePlay
    );
    EmitSleepControllerLoop(
        il,
        findSleepControllers,
        sleepArrayLocal,
        sleepIndexLocal,
        sleepLocal,
        setSleeping,
        sleeping: false
    );
    EmitControllerLoop(il, findControllers, controllerArrayLocal, controllerIndexLocal, controllerLocal, new[]
    {
        (MethodDefinition)setDancing,
        (MethodDefinition)setDragging,
    }, dancing: false, dragging: false);
    il.Append(il.Create(OpCodes.Br, ret));

    il.Append(notStop);
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, isDragAction));
    il.Append(il.Create(OpCodes.Brfalse, notDrag));
    EmitControllerLoop(il, findControllers, controllerArrayLocal, controllerIndexLocal, controllerLocal, new[]
    {
        (MethodDefinition)setDragging,
    }, dancing: null, dragging: true);
    il.Append(il.Create(OpCodes.Br, ret));

    il.Append(notDrag);
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Call, isInteractAction));
    il.Append(il.Create(OpCodes.Brfalse, notInteract));
    EmitFoodLoop(il, findFoods, foodArrayLocal, foodIndexLocal, foodLocal, playInteract);
    il.Append(il.Create(OpCodes.Br, ret));

    il.Append(notInteract);
    il.Append(ret);
    changed = true;
    return method;
}

static void EmitDanceHandlerPlayLoop(
    ILProcessor il,
    GenericInstanceMethod findDanceHandlers,
    VariableDefinition arrayLocal,
    VariableDefinition indexLocal,
    VariableDefinition handlerLocal,
    MethodDefinition playIndex,
    int danceIndex
)
{
    var loopStart = il.Create(OpCodes.Nop);
    var loopContinue = il.Create(OpCodes.Nop);
    var loopCheck = il.Create(OpCodes.Nop);

    il.Append(il.Create(OpCodes.Call, findDanceHandlers));
    il.Append(il.Create(OpCodes.Stloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_0));
    il.Append(il.Create(OpCodes.Stloc, indexLocal));
    il.Append(il.Create(OpCodes.Br, loopCheck));
    il.Append(loopStart);
    il.Append(il.Create(OpCodes.Ldloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldelem_Ref));
    il.Append(il.Create(OpCodes.Stloc, handlerLocal));
    il.Append(il.Create(OpCodes.Ldloc, handlerLocal));
    il.Append(il.Create(OpCodes.Brfalse, loopContinue));
    il.Append(il.Create(OpCodes.Ldloc, handlerLocal));
    il.Append(il.Create(OpCodes.Ldc_I4, danceIndex));
    il.Append(il.Create(OpCodes.Callvirt, playIndex));
    il.Append(loopContinue);
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Add));
    il.Append(il.Create(OpCodes.Stloc, indexLocal));
    il.Append(loopCheck);
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldlen));
    il.Append(il.Create(OpCodes.Conv_I4));
    il.Append(il.Create(OpCodes.Blt, loopStart));
}

static void EmitDanceHandlerStopLoop(
    ILProcessor il,
    GenericInstanceMethod findDanceHandlers,
    VariableDefinition arrayLocal,
    VariableDefinition indexLocal,
    VariableDefinition handlerLocal,
    MethodDefinition stopPlay
)
{
    var loopStart = il.Create(OpCodes.Nop);
    var loopContinue = il.Create(OpCodes.Nop);
    var loopCheck = il.Create(OpCodes.Nop);

    il.Append(il.Create(OpCodes.Call, findDanceHandlers));
    il.Append(il.Create(OpCodes.Stloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_0));
    il.Append(il.Create(OpCodes.Stloc, indexLocal));
    il.Append(il.Create(OpCodes.Br, loopCheck));
    il.Append(loopStart);
    il.Append(il.Create(OpCodes.Ldloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldelem_Ref));
    il.Append(il.Create(OpCodes.Stloc, handlerLocal));
    il.Append(il.Create(OpCodes.Ldloc, handlerLocal));
    il.Append(il.Create(OpCodes.Brfalse, loopContinue));
    il.Append(il.Create(OpCodes.Ldloc, handlerLocal));
    il.Append(il.Create(OpCodes.Callvirt, stopPlay));
    il.Append(loopContinue);
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Add));
    il.Append(il.Create(OpCodes.Stloc, indexLocal));
    il.Append(loopCheck);
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldlen));
    il.Append(il.Create(OpCodes.Conv_I4));
    il.Append(il.Create(OpCodes.Blt, loopStart));
}

static void EmitSleepControllerLoop(
    ILProcessor il,
    GenericInstanceMethod findSleepControllers,
    VariableDefinition arrayLocal,
    VariableDefinition indexLocal,
    VariableDefinition sleepLocal,
    MethodDefinition setSleeping,
    bool sleeping
)
{
    var loopStart = il.Create(OpCodes.Nop);
    var loopContinue = il.Create(OpCodes.Nop);
    var loopCheck = il.Create(OpCodes.Nop);

    il.Append(il.Create(OpCodes.Call, findSleepControllers));
    il.Append(il.Create(OpCodes.Stloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_0));
    il.Append(il.Create(OpCodes.Stloc, indexLocal));
    il.Append(il.Create(OpCodes.Br, loopCheck));
    il.Append(loopStart);
    il.Append(il.Create(OpCodes.Ldloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldelem_Ref));
    il.Append(il.Create(OpCodes.Stloc, sleepLocal));
    il.Append(il.Create(OpCodes.Ldloc, sleepLocal));
    il.Append(il.Create(OpCodes.Brfalse, loopContinue));
    il.Append(il.Create(OpCodes.Ldloc, sleepLocal));
    il.Append(il.Create(sleeping ? OpCodes.Ldc_I4_1 : OpCodes.Ldc_I4_0));
    il.Append(il.Create(OpCodes.Callvirt, setSleeping));
    il.Append(loopContinue);
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Add));
    il.Append(il.Create(OpCodes.Stloc, indexLocal));
    il.Append(loopCheck);
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldlen));
    il.Append(il.Create(OpCodes.Conv_I4));
    il.Append(il.Create(OpCodes.Blt, loopStart));
}

static void EmitLocomotionWalkLoop(
    ILProcessor il,
    GenericInstanceMethod findLocomotionControllers,
    VariableDefinition arrayLocal,
    VariableDefinition indexLocal,
    VariableDefinition locomotionLocal,
    MethodDefinition walkMethod,
    bool? direction
)
{
    var loopStart = il.Create(OpCodes.Nop);
    var loopContinue = il.Create(OpCodes.Nop);
    var loopCheck = il.Create(OpCodes.Nop);

    il.Append(il.Create(OpCodes.Call, findLocomotionControllers));
    il.Append(il.Create(OpCodes.Stloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_0));
    il.Append(il.Create(OpCodes.Stloc, indexLocal));
    il.Append(il.Create(OpCodes.Br, loopCheck));
    il.Append(loopStart);
    il.Append(il.Create(OpCodes.Ldloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldelem_Ref));
    il.Append(il.Create(OpCodes.Stloc, locomotionLocal));
    il.Append(il.Create(OpCodes.Ldloc, locomotionLocal));
    il.Append(il.Create(OpCodes.Brfalse, loopContinue));
    il.Append(il.Create(OpCodes.Ldloc, locomotionLocal));
    if (direction.HasValue)
    {
        il.Append(il.Create(direction.Value ? OpCodes.Ldc_I4_1 : OpCodes.Ldc_I4_0));
    }
    il.Append(il.Create(OpCodes.Callvirt, walkMethod));
    il.Append(loopContinue);
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Add));
    il.Append(il.Create(OpCodes.Stloc, indexLocal));
    il.Append(loopCheck);
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldlen));
    il.Append(il.Create(OpCodes.Conv_I4));
    il.Append(il.Create(OpCodes.Blt, loopStart));
}

static void EmitControllerIndexedDanceLoop(
    ILProcessor il,
    GenericInstanceMethod findControllers,
    VariableDefinition arrayLocal,
    VariableDefinition indexLocal,
    VariableDefinition controllerLocal,
    MethodDefinition setDragging,
    MethodDefinition setDancing,
    FieldDefinition danceStateField,
    FieldDefinition animatorField,
    FieldDefinition danceIndexParamField,
    MethodReference animatorSetFloat,
    int danceIndex
)
{
    var loopStart = il.Create(OpCodes.Nop);
    var loopContinue = il.Create(OpCodes.Nop);
    var loopCheck = il.Create(OpCodes.Nop);
    var skipAnimator = il.Create(OpCodes.Nop);

    il.Append(il.Create(OpCodes.Call, findControllers));
    il.Append(il.Create(OpCodes.Stloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_0));
    il.Append(il.Create(OpCodes.Stloc, indexLocal));
    il.Append(il.Create(OpCodes.Br, loopCheck));
    il.Append(loopStart);
    il.Append(il.Create(OpCodes.Ldloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldelem_Ref));
    il.Append(il.Create(OpCodes.Stloc, controllerLocal));
    il.Append(il.Create(OpCodes.Ldloc, controllerLocal));
    il.Append(il.Create(OpCodes.Brfalse, loopContinue));

    il.Append(il.Create(OpCodes.Ldloc, controllerLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_0));
    il.Append(il.Create(OpCodes.Callvirt, setDragging));
    il.Append(il.Create(OpCodes.Ldloc, controllerLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Callvirt, setDancing));
    il.Append(il.Create(OpCodes.Ldloc, controllerLocal));
    il.Append(il.Create(OpCodes.Ldc_I4, danceIndex));
    il.Append(il.Create(OpCodes.Stfld, danceStateField));
    il.Append(il.Create(OpCodes.Ldloc, controllerLocal));
    il.Append(il.Create(OpCodes.Ldfld, animatorField));
    il.Append(il.Create(OpCodes.Brfalse, skipAnimator));
    il.Append(il.Create(OpCodes.Ldloc, controllerLocal));
    il.Append(il.Create(OpCodes.Ldfld, animatorField));
    il.Append(il.Create(OpCodes.Ldsfld, danceIndexParamField));
    il.Append(il.Create(OpCodes.Ldc_R4, (float)danceIndex));
    il.Append(il.Create(OpCodes.Callvirt, animatorSetFloat));
    il.Append(skipAnimator);

    il.Append(loopContinue);
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Add));
    il.Append(il.Create(OpCodes.Stloc, indexLocal));
    il.Append(loopCheck);
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldlen));
    il.Append(il.Create(OpCodes.Conv_I4));
    il.Append(il.Create(OpCodes.Blt, loopStart));
}

static void EmitControllerLoop(
    ILProcessor il,
    GenericInstanceMethod findControllers,
    VariableDefinition arrayLocal,
    VariableDefinition indexLocal,
    VariableDefinition controllerLocal,
    IReadOnlyList<MethodDefinition> methods,
    bool? dancing,
    bool? dragging
)
{
    var loopStart = il.Create(OpCodes.Nop);
    var loopContinue = il.Create(OpCodes.Nop);
    var loopCheck = il.Create(OpCodes.Nop);

    il.Append(il.Create(OpCodes.Call, findControllers));
    il.Append(il.Create(OpCodes.Stloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_0));
    il.Append(il.Create(OpCodes.Stloc, indexLocal));
    il.Append(il.Create(OpCodes.Br, loopCheck));
    il.Append(loopStart);
    il.Append(il.Create(OpCodes.Ldloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldelem_Ref));
    il.Append(il.Create(OpCodes.Stloc, controllerLocal));
    il.Append(il.Create(OpCodes.Ldloc, controllerLocal));
    il.Append(il.Create(OpCodes.Brfalse, loopContinue));

    foreach (var method in methods)
    {
        il.Append(il.Create(OpCodes.Ldloc, controllerLocal));
        if (method.Parameters.Count == 1 && method.Parameters[0].ParameterType.FullName == "System.Boolean")
        {
            var value = method.Name == "SetDancing" ? dancing.GetValueOrDefault(true) : dragging.GetValueOrDefault(true);
            il.Append(il.Create(value ? OpCodes.Ldc_I4_1 : OpCodes.Ldc_I4_0));
        }
        il.Append(il.Create(OpCodes.Callvirt, method));
    }

    il.Append(loopContinue);
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Add));
    il.Append(il.Create(OpCodes.Stloc, indexLocal));
    il.Append(loopCheck);
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldlen));
    il.Append(il.Create(OpCodes.Conv_I4));
    il.Append(il.Create(OpCodes.Blt, loopStart));
}

static void EmitFoodLoop(
    ILProcessor il,
    GenericInstanceMethod findFoods,
    VariableDefinition arrayLocal,
    VariableDefinition indexLocal,
    VariableDefinition foodLocal,
    MethodDefinition playInteract
)
{
    var loopStart = il.Create(OpCodes.Nop);
    var loopContinue = il.Create(OpCodes.Nop);
    var loopCheck = il.Create(OpCodes.Nop);

    il.Append(il.Create(OpCodes.Call, findFoods));
    il.Append(il.Create(OpCodes.Stloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_0));
    il.Append(il.Create(OpCodes.Stloc, indexLocal));
    il.Append(il.Create(OpCodes.Br, loopCheck));
    il.Append(loopStart);
    il.Append(il.Create(OpCodes.Ldloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldelem_Ref));
    il.Append(il.Create(OpCodes.Stloc, foodLocal));
    il.Append(il.Create(OpCodes.Ldloc, foodLocal));
    il.Append(il.Create(OpCodes.Brfalse, loopContinue));
    il.Append(il.Create(OpCodes.Ldloc, foodLocal));
    il.Append(il.Create(OpCodes.Callvirt, playInteract));
    il.Append(loopContinue);
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldc_I4_1));
    il.Append(il.Create(OpCodes.Add));
    il.Append(il.Create(OpCodes.Stloc, indexLocal));
    il.Append(loopCheck);
    il.Append(il.Create(OpCodes.Ldloc, indexLocal));
    il.Append(il.Create(OpCodes.Ldloc, arrayLocal));
    il.Append(il.Create(OpCodes.Ldlen));
    il.Append(il.Create(OpCodes.Conv_I4));
    il.Append(il.Create(OpCodes.Blt, loopStart));
}

static bool PatchMouthEvent(ModuleDefinition module, MethodDefinition applyMouth)
{
    var targetType = FindType(module, "AvatarMinecraftMessages");
    var proxEventType = FindType(module, "AvatarMinecraftMessages/ProxEvent");
    if (targetType == null || proxEventType == null)
    {
        Console.Error.WriteLine("AvatarMinecraftMessages or ProxEvent not found.");
        Environment.ExitCode = 5;
        return false;
    }

    var update = targetType.Methods.FirstOrDefault(method => method.Name == "Update" && method.HasBody);
    if (update == null)
    {
        Console.Error.WriteLine("AvatarMinecraftMessages.Update() not found.");
        Environment.ExitCode = 6;
        return false;
    }
    if (update.Body.Instructions.Any(instruction => instruction.OpCode == OpCodes.Ldstr && Equals(instruction.Operand, MouthEventType)))
    {
        return false;
    }

    var typeField = proxEventType.Fields.First(field => field.Name == "type");
    var distanceField = proxEventType.Fields.First(field => field.Name == "distance");
    var stringEquality = module.TypeSystem.String.Resolve().Methods.First(method =>
        method.Name == "op_Equality"
        && method.Parameters.Count == 2
        && method.Parameters[0].ParameterType.FullName == module.TypeSystem.String.FullName
        && method.Parameters[1].ParameterType.FullName == module.TypeSystem.String.FullName
    );

    var instructions = update.Body.Instructions;
    var mobTypeIndex = instructions
        .Select((instruction, index) => new { instruction, index })
        .FirstOrDefault(row => row.instruction.OpCode == OpCodes.Ldstr && Equals(row.instruction.Operand, "mob_proximity"))
        ?.index ?? -1;
    if (mobTypeIndex < 2)
    {
        Console.Error.WriteLine("Could not locate mob_proximity branch in AvatarMinecraftMessages.Update().");
        Environment.ExitCode = 7;
        return false;
    }

    var insertBefore = instructions[mobTypeIndex - 2];
    var first = instructions.First();
    var il = update.Body.GetILProcessor();
    var continueInstruction = il.Create(OpCodes.Nop);
    var injected = new[]
    {
        il.Create(OpCodes.Ldloc_1),
        il.Create(OpCodes.Ldfld, typeField),
        il.Create(OpCodes.Ldstr, MouthEventType),
        il.Create(OpCodes.Call, module.ImportReference(stringEquality)),
        il.Create(OpCodes.Brfalse, continueInstruction),
        il.Create(OpCodes.Ldloc_1),
        il.Create(OpCodes.Ldfld, distanceField),
        il.Create(OpCodes.Call, applyMouth),
        il.Create(OpCodes.Br, first),
        continueInstruction,
    };

    foreach (var instruction in injected)
    {
        il.InsertBefore(insertBefore, instruction);
    }
    return true;
}

static bool PatchFormEvent(ModuleDefinition module, MethodDefinition applyForm)
{
    var targetType = FindType(module, "AvatarMinecraftMessages");
    var proxEventType = FindType(module, "AvatarMinecraftMessages/ProxEvent");
    if (targetType == null || proxEventType == null)
    {
        Console.Error.WriteLine("AvatarMinecraftMessages or ProxEvent not found.");
        Environment.ExitCode = 11;
        return false;
    }

    var update = targetType.Methods.FirstOrDefault(method => method.Name == "Update" && method.HasBody);
    if (update == null)
    {
        Console.Error.WriteLine("AvatarMinecraftMessages.Update() not found.");
        Environment.ExitCode = 12;
        return false;
    }
    if (update.Body.Instructions.Any(instruction => instruction.OpCode == OpCodes.Ldstr && Equals(instruction.Operand, FormEventType)))
    {
        return false;
    }

    var typeField = proxEventType.Fields.First(field => field.Name == "type");
    var distanceField = proxEventType.Fields.First(field => field.Name == "distance");
    var nameField = proxEventType.Fields.First(field => field.Name == "name");
    var stringEquality = module.TypeSystem.String.Resolve().Methods.First(method =>
        method.Name == "op_Equality"
        && method.Parameters.Count == 2
        && method.Parameters[0].ParameterType.FullName == module.TypeSystem.String.FullName
        && method.Parameters[1].ParameterType.FullName == module.TypeSystem.String.FullName
    );

    var instructions = update.Body.Instructions;
    var mobTypeIndex = instructions
        .Select((instruction, index) => new { instruction, index })
        .FirstOrDefault(row => row.instruction.OpCode == OpCodes.Ldstr && Equals(row.instruction.Operand, "mob_proximity"))
        ?.index ?? -1;
    if (mobTypeIndex < 2)
    {
        Console.Error.WriteLine("Could not locate mob_proximity branch in AvatarMinecraftMessages.Update().");
        Environment.ExitCode = 13;
        return false;
    }

    var insertBefore = instructions[mobTypeIndex - 2];
    var first = instructions.First();
    var il = update.Body.GetILProcessor();
    var continueInstruction = il.Create(OpCodes.Nop);
    var injected = new[]
    {
        il.Create(OpCodes.Ldloc_1),
        il.Create(OpCodes.Ldfld, typeField),
        il.Create(OpCodes.Ldstr, FormEventType),
        il.Create(OpCodes.Call, module.ImportReference(stringEquality)),
        il.Create(OpCodes.Brfalse, continueInstruction),
        il.Create(OpCodes.Ldloc_1),
        il.Create(OpCodes.Ldfld, nameField),
        il.Create(OpCodes.Ldloc_1),
        il.Create(OpCodes.Ldfld, distanceField),
        il.Create(OpCodes.Call, applyForm),
        il.Create(OpCodes.Br, first),
        continueInstruction,
    };

    foreach (var instruction in injected)
    {
        il.InsertBefore(insertBefore, instruction);
    }
    return true;
}

static bool PatchFreezeEvent(ModuleDefinition module, MethodDefinition applyFreeze)
{
    var targetType = FindType(module, "AvatarMinecraftMessages");
    var proxEventType = FindType(module, "AvatarMinecraftMessages/ProxEvent");
    if (targetType == null || proxEventType == null)
    {
        Console.Error.WriteLine("AvatarMinecraftMessages or ProxEvent not found.");
        Environment.ExitCode = 14;
        return false;
    }

    var update = targetType.Methods.FirstOrDefault(method => method.Name == "Update" && method.HasBody);
    if (update == null)
    {
        Console.Error.WriteLine("AvatarMinecraftMessages.Update() not found.");
        Environment.ExitCode = 15;
        return false;
    }
    if (update.Body.Instructions.Any(instruction => instruction.OpCode == OpCodes.Ldstr && Equals(instruction.Operand, FreezeEventType)))
    {
        return false;
    }

    var typeField = proxEventType.Fields.First(field => field.Name == "type");
    var distanceField = proxEventType.Fields.First(field => field.Name == "distance");
    var stringEquality = module.TypeSystem.String.Resolve().Methods.First(method =>
        method.Name == "op_Equality"
        && method.Parameters.Count == 2
        && method.Parameters[0].ParameterType.FullName == module.TypeSystem.String.FullName
        && method.Parameters[1].ParameterType.FullName == module.TypeSystem.String.FullName
    );

    var instructions = update.Body.Instructions;
    var mobTypeIndex = instructions
        .Select((instruction, index) => new { instruction, index })
        .FirstOrDefault(row => row.instruction.OpCode == OpCodes.Ldstr && Equals(row.instruction.Operand, "mob_proximity"))
        ?.index ?? -1;
    if (mobTypeIndex < 2)
    {
        Console.Error.WriteLine("Could not locate mob_proximity branch in AvatarMinecraftMessages.Update().");
        Environment.ExitCode = 16;
        return false;
    }

    var insertBefore = instructions[mobTypeIndex - 2];
    var first = instructions.First();
    var il = update.Body.GetILProcessor();
    var continueInstruction = il.Create(OpCodes.Nop);
    var injected = new[]
    {
        il.Create(OpCodes.Ldloc_1),
        il.Create(OpCodes.Ldfld, typeField),
        il.Create(OpCodes.Ldstr, FreezeEventType),
        il.Create(OpCodes.Call, module.ImportReference(stringEquality)),
        il.Create(OpCodes.Brfalse, continueInstruction),
        il.Create(OpCodes.Ldloc_1),
        il.Create(OpCodes.Ldfld, distanceField),
        il.Create(OpCodes.Call, applyFreeze),
        il.Create(OpCodes.Br, first),
        continueInstruction,
    };

    foreach (var instruction in injected)
    {
        il.InsertBefore(insertBefore, instruction);
    }
    return true;
}

static bool PatchActionEvent(ModuleDefinition module, MethodDefinition applyAction)
{
    var targetType = FindType(module, "AvatarMinecraftMessages");
    var proxEventType = FindType(module, "AvatarMinecraftMessages/ProxEvent");
    if (targetType == null || proxEventType == null)
    {
        Console.Error.WriteLine("AvatarMinecraftMessages or ProxEvent not found.");
        Environment.ExitCode = 17;
        return false;
    }

    var update = targetType.Methods.FirstOrDefault(method => method.Name == "Update" && method.HasBody);
    if (update == null)
    {
        Console.Error.WriteLine("AvatarMinecraftMessages.Update() not found.");
        Environment.ExitCode = 18;
        return false;
    }
    if (update.Body.Instructions.Any(instruction => instruction.OpCode == OpCodes.Ldstr && Equals(instruction.Operand, ActionEventType)))
    {
        return false;
    }

    var typeField = proxEventType.Fields.First(field => field.Name == "type");
    var distanceField = proxEventType.Fields.First(field => field.Name == "distance");
    var nameField = proxEventType.Fields.First(field => field.Name == "name");
    var stringEquality = module.TypeSystem.String.Resolve().Methods.First(method =>
        method.Name == "op_Equality"
        && method.Parameters.Count == 2
        && method.Parameters[0].ParameterType.FullName == module.TypeSystem.String.FullName
        && method.Parameters[1].ParameterType.FullName == module.TypeSystem.String.FullName
    );

    var instructions = update.Body.Instructions;
    var mobTypeIndex = instructions
        .Select((instruction, index) => new { instruction, index })
        .FirstOrDefault(row => row.instruction.OpCode == OpCodes.Ldstr && Equals(row.instruction.Operand, "mob_proximity"))
        ?.index ?? -1;
    if (mobTypeIndex < 2)
    {
        Console.Error.WriteLine("Could not locate mob_proximity branch in AvatarMinecraftMessages.Update().");
        Environment.ExitCode = 19;
        return false;
    }

    var insertBefore = instructions[mobTypeIndex - 2];
    var first = instructions.First();
    var il = update.Body.GetILProcessor();
    var continueInstruction = il.Create(OpCodes.Nop);
    var injected = new[]
    {
        il.Create(OpCodes.Ldloc_1),
        il.Create(OpCodes.Ldfld, typeField),
        il.Create(OpCodes.Ldstr, ActionEventType),
        il.Create(OpCodes.Call, module.ImportReference(stringEquality)),
        il.Create(OpCodes.Brfalse, continueInstruction),
        il.Create(OpCodes.Ldloc_1),
        il.Create(OpCodes.Ldfld, nameField),
        il.Create(OpCodes.Ldloc_1),
        il.Create(OpCodes.Ldfld, distanceField),
        il.Create(OpCodes.Call, applyAction),
        il.Create(OpCodes.Br, first),
        continueInstruction,
    };

    foreach (var instruction in injected)
    {
        il.InsertBefore(insertBefore, instruction);
    }
    return true;
}

static bool PatchUniversalBlendshapesLateUpdate(
    ModuleDefinition module,
    MethodDefinition applyFormFrame,
    MethodDefinition applyFormInputs,
    MethodDefinition applyFreezeFrame
)
{
    var universalBlendshapes = FindType(module, "UniversalBlendshapes");
    var runtimeType = FindType(module, RuntimeTypeName);
    if (universalBlendshapes == null || runtimeType == null)
    {
        Console.Error.WriteLine("UniversalBlendshapes or Natsume runtime type not found.");
        Environment.ExitCode = 8;
        return false;
    }

    var currentMouthField = runtimeType.Fields.FirstOrDefault(field =>
        field.Name == CurrentMouthFieldName
        && field.FieldType.FullName == module.TypeSystem.Single.FullName
    );
    if (currentMouthField == null)
    {
        Console.Error.WriteLine("Natsume current mouth field not found.");
        Environment.ExitCode = 9;
        return false;
    }

    var lateUpdate = universalBlendshapes.Methods.FirstOrDefault(method => method.Name == "LateUpdate" && method.HasBody);
    if (lateUpdate == null)
    {
        Console.Error.WriteLine("UniversalBlendshapes.LateUpdate() not found.");
        Environment.ExitCode = 10;
        return false;
    }
    var changed = false;
    var hasMouthPatch = lateUpdate.Body.Instructions.Any(instruction =>
        instruction.OpCode == OpCodes.Ldsfld
        && instruction.Operand is FieldReference fieldReference
        && fieldReference.Name == CurrentMouthFieldName
    );

    var mouthFields = new[]
    {
        universalBlendshapes.Fields.First(field => field.Name == "A"),
        universalBlendshapes.Fields.First(field => field.Name == "I"),
        universalBlendshapes.Fields.First(field => field.Name == "U"),
        universalBlendshapes.Fields.First(field => field.Name == "E"),
        universalBlendshapes.Fields.First(field => field.Name == "O"),
    };

    var il = lateUpdate.Body.GetILProcessor();
    var first = lateUpdate.Body.Instructions.First();
    var hasFreezeFramePatch = lateUpdate.Body.Instructions.Any(instruction =>
        instruction.OpCode == OpCodes.Call
        && instruction.Operand is MethodReference methodReference
        && methodReference.Name == ApplyFreezeFrameMethodName
    );
    if (!hasFreezeFramePatch)
    {
        il.InsertBefore(first, il.Create(OpCodes.Call, applyFreezeFrame));
        changed = true;
    }

    if (!hasMouthPatch)
    {
        var injected = new List<Instruction>
        {
            il.Create(OpCodes.Ldarg_0),
            il.Create(OpCodes.Ldsfld, currentMouthField),
            il.Create(OpCodes.Stfld, mouthFields[0]),
        };
        for (var i = 1; i < mouthFields.Length; i++)
        {
            injected.Add(il.Create(OpCodes.Ldarg_0));
            if (i == 4)
            {
                injected.Add(il.Create(OpCodes.Ldsfld, currentMouthField));
            }
            else
            {
                injected.Add(il.Create(OpCodes.Ldc_R4, 0.0f));
            }
            injected.Add(il.Create(OpCodes.Stfld, mouthFields[i]));
        }

        foreach (var instruction in injected)
        {
            il.InsertBefore(first, instruction);
        }
        changed = true;
    }

    var hasFormFrameStartPatch = lateUpdate.Body.Instructions.Any(instruction =>
        instruction.OpCode == OpCodes.Call
        && instruction.Operand is MethodReference methodReference
        && methodReference.Name == ApplyFormFrameMethodName
    );
    if (!hasFormFrameStartPatch)
    {
        il.InsertBefore(first, il.Create(OpCodes.Call, applyFormFrame));
        changed = true;
    }

    var hasFormInputsPatch = lateUpdate.Body.Instructions.Any(instruction =>
        instruction.OpCode == OpCodes.Call
        && instruction.Operand is MethodReference methodReference
        && methodReference.Name == ApplyFormInputsMethodName
    );
    if (!hasFormInputsPatch)
    {
        var insertBeforeFormInput = lateUpdate.Body.Instructions.FirstOrDefault(instruction =>
            instruction.OpCode == OpCodes.Call
            && instruction.Operand is MethodReference methodReference
            && methodReference.Name == "get_time"
            && methodReference.DeclaringType.FullName == "UnityEngine.Time"
        ) ?? first;
        il.InsertBefore(insertBeforeFormInput, il.Create(OpCodes.Ldarg_0));
        il.InsertBefore(insertBeforeFormInput, il.Create(OpCodes.Call, applyFormInputs));
        changed = true;
    }

    var hasFormFrameEndPatch = lateUpdate.Body.Instructions
        .Select((instruction, index) => new { instruction, index })
        .Any(row =>
            row.instruction.OpCode == OpCodes.Call
            && row.instruction.Operand is MethodReference methodReference
            && methodReference.Name == ApplyFormFrameMethodName
            && row.index + 1 < lateUpdate.Body.Instructions.Count
            && lateUpdate.Body.Instructions[row.index + 1].OpCode == OpCodes.Ret
        );
    if (!hasFormFrameEndPatch)
    {
        var returns = lateUpdate.Body.Instructions
            .Where(instruction => instruction.OpCode == OpCodes.Ret)
            .ToList();
        foreach (var returnInstruction in returns)
        {
            il.InsertBefore(returnInstruction, il.Create(OpCodes.Call, applyFormFrame));
        }
        changed = true;
    }
    return changed;
}

static MethodDefinition EnsureStringMatchMethod(
    ModuleDefinition module,
    TypeDefinition runtimeType,
    string methodName,
    IReadOnlyList<string> exactNames,
    ref bool changed
)
{
    var method = runtimeType.Methods.FirstOrDefault(candidate =>
        candidate.Name == methodName
        && candidate.Parameters.Count == 1
        && candidate.Parameters[0].ParameterType.FullName == module.TypeSystem.String.FullName
    );
    if (method == null)
    {
        method = new MethodDefinition(
            methodName,
            MethodAttributes.Private | MethodAttributes.Static | MethodAttributes.HideBySig,
            module.TypeSystem.Boolean
        );
        method.Parameters.Add(new ParameterDefinition("value", ParameterAttributes.None, module.TypeSystem.String));
        runtimeType.Methods.Add(method);
    }
    else
    {
        method.Body = new MethodBody(method);
    }

    var stringEquality = module.TypeSystem.String.Resolve().Methods.First(candidate =>
        candidate.Name == "op_Equality"
        && candidate.Parameters.Count == 2
        && candidate.Parameters[0].ParameterType.FullName == module.TypeSystem.String.FullName
        && candidate.Parameters[1].ParameterType.FullName == module.TypeSystem.String.FullName
    );

    method.Body.InitLocals = false;
    var il = method.Body.GetILProcessor();
    foreach (var name in exactNames.Where(item => !string.IsNullOrWhiteSpace(item)).Distinct(StringComparer.Ordinal))
    {
        var next = il.Create(OpCodes.Nop);
        il.Append(il.Create(OpCodes.Ldarg_0));
        il.Append(il.Create(OpCodes.Ldstr, name));
        il.Append(il.Create(OpCodes.Call, module.ImportReference(stringEquality)));
        il.Append(il.Create(OpCodes.Brfalse, next));
        il.Append(il.Create(OpCodes.Ldc_I4_1));
        il.Append(il.Create(OpCodes.Ret));
        il.Append(next);
    }
    il.Append(il.Create(OpCodes.Ldc_I4_0));
    il.Append(il.Create(OpCodes.Ret));

    changed = true;
    return method;
}

static TypeDefinition? FindType(ModuleDefinition module, string fullNameOrName)
{
    return AllTypes(module.Types).FirstOrDefault(type =>
        type.FullName == fullNameOrName || type.Name == fullNameOrName
    );
}

static IEnumerable<TypeDefinition> AllTypes(IEnumerable<TypeDefinition> types)
{
    foreach (var type in types)
    {
        yield return type;
        foreach (var nested in AllTypes(type.NestedTypes))
        {
            yield return nested;
        }
    }
}

static MethodReference? FindGenericMethodTemplate(ModuleDefinition module, string name, int parameterCount)
{
    foreach (var methodReference in EnumerateMethodReferences(module))
    {
        if (methodReference is not GenericInstanceMethod genericMethod)
        {
            continue;
        }
        if (genericMethod.ElementMethod.Name == name && genericMethod.ElementMethod.Parameters.Count == parameterCount)
        {
            return genericMethod.ElementMethod;
        }
    }
    return null;
}

static MethodReference? FindMethodReference(ModuleDefinition module, Func<MethodReference, bool> predicate)
{
    return EnumerateMethodReferences(module).FirstOrDefault(predicate);
}

static IEnumerable<MethodReference> EnumerateMethodReferences(ModuleDefinition module)
{
    foreach (var type in AllTypes(module.Types))
    {
        foreach (var method in type.Methods)
        {
            if (!method.HasBody)
            {
                continue;
            }
            foreach (var instruction in method.Body.Instructions)
            {
                if (instruction.Operand is MethodReference methodReference)
                {
                    yield return methodReference;
                }
            }
        }
    }
}
