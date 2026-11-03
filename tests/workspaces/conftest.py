import copy
import pytest
from lookandpick.workspaces.repository import Repository
from lookandpick.workspaces.schema import IDENTITY,settings

@pytest.fixture
def workspace(tmp_path):
    repo=Repository(tmp_path);value=repo.create('Test tabletop');folder=repo.folder(value['id']);(folder/'result.splat').write_bytes(b'valid fixture')
    surface={'name':'table','centre':[0.,0.,0.],'width':1.,'depth':.6,'yaw':0.,'height':0.,'enabled':True}
    draft={'schema_version':1,'id':value['id'],'name':value['name'],'assets':{'scene.splat':'result.splat'},'surfaces':[surface], 'fitted_surfaces':[copy.deepcopy(surface)],
           'frames':{'canonical_from_map':copy.deepcopy(IDENTITY),'workspace_from_map':copy.deepcopy(IDENTITY)},'calibration':{'meters_per_map_unit':None,'scale_status':'relative','robot_alignment_status':'approximate'},
           'robot':{'translation':[0.,0.,0.],'rotation':[0.,0.,0.],'scale':1.,'joint_positions':{}},'layers':dict.fromkeys(['background','surfaces','robot','observed'],True),'needs_rebuild':False,'settings':settings(),'units':'relative'}
    value['draft']=draft;repo.write(value);return repo,value

