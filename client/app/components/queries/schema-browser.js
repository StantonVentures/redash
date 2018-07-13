import template from './schema-browser.html';

function SchemaBrowserCtrl($rootScope, $scope, toastr) {
  'ngInject';

  $scope.schemaFilterColumn = '';
  $scope.schemaFilterTable = '';

  this.showTable = (table) => {
    table.collapsed = !table.collapsed;
    $scope.$broadcast('vsRepeatTrigger');
  };

  this.getSize = (table) => {
    let size = 22;

    if (!table.collapsed) {
      size += 18 * table.columns.length;
    }

    return size;
  };

  this.isEmpty = function isEmpty() {
    return this.schema === undefined || this.schema.length === 0;
  };

  this.itemSelected = ($event, hierarchy) => {
    $rootScope.$broadcast('query-editor.paste', hierarchy.join('.'));
    $event.preventDefault();
    $event.stopPropagation();
  };

  this.splitFilter = (filter) => {
    const splitTheFilter = filter.split(' ');
    $scope.schemaFilterColumn = '';
    $scope.schemaFilterTable = '';
    console.log(splitTheFilter);
    if (splitTheFilter.length >= 3 || filter.indexOf('  ') >= 0) {
      toastr.warning('Only 1 space is allowed in the schema search box.');
    }
    $scope.schemaFilterTable = splitTheFilter[0];
    if (splitTheFilter[1] !== undefined) {
      $scope.schemaFilterColumn = splitTheFilter[1];
    }
    console.log($scope.schemaFilterTable);
    console.log($scope.schemaFilterColumn);
  };
}

const SchemaBrowser = {
  bindings: {
    schema: '<',
    onRefresh: '&',
  },
  controller: SchemaBrowserCtrl,
  template,
};

export default function init(ngModule) {
  ngModule.component('schemaBrowser', SchemaBrowser);
}
